# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""The `windows_sdk` module provides safe functions to access a hermetic
Microsoft Visual Studio installation.

Available only to Google-run bots.
"""

import collections
from contextlib import contextmanager

from recipe_engine import recipe_api


class WindowsSDKApi(recipe_api.RecipeApi):
  """API for using Windows SDK distributed via CIPD."""

  SDKPaths = collections.namedtuple('SDKPaths', ['win_sdk', 'dia_sdk'])

  def __init__(self, sdk_properties, *args, **kwargs):
    super(WindowsSDKApi, self).__init__(*args, **kwargs)

    self._sdk_properties = sdk_properties

  @contextmanager
  def __call__(self, path=None, version=None, enabled=True, target_arch='x64'):
    """Sets up the SDK environment when enabled.

    Args:
      * path (path): Path to a directory where to install the SDK
        (default is '[CACHE]/windows_sdk')
      * version (str): CIPD version of the SDK
        (default is set via $infra/windows_sdk.version property)
      * enabled (bool): Whether the SDK should be used or not.
      * target_arch (str): 'x86' or 'x64'.

    Yields:
      If enabled, yields SDKPaths object with paths to well-known roots within
      the deployed bundle:
        * win_sdk - a Path to the root of the extracted Windows SDK.
        * dia_sdk - a Path to the root of the extracted Debug Interface Access
          SDK.

    Raises:
        StepFailure or InfraFailure.
    """
    if enabled:
      sdk_dir = self._ensure_sdk(
          path or self.m.path['cache'].join('windows_sdk'),
          version or self._sdk_properties['version'])
      try:
        with self.m.context(**self._sdk_env(sdk_dir, target_arch)):
          yield WindowsSDKApi.SDKPaths(
              sdk_dir.join('win_sdk'),
              sdk_dir.join('DIA SDK'))
      finally:
        # cl.exe automatically starts background mspdbsrv.exe daemon which
        # needs to be manually stopped so Swarming can tidy up after itself.
        #
        # Since mspdbsrv may not actually be running, don't fail if we can't
        # actually kill it.
        self.m.step('taskkill mspdbsrv',
                    ['taskkill.exe', '/f', '/t', '/im', 'mspdbsrv.exe'],
                    ok_ret='any')
    else:
      yield

  def _ensure_sdk(self, sdk_dir, sdk_version):
    """Ensures the Windows SDK CIPD package is installed.

    Returns the directory where the SDK package has been installed.

    Args:
      * path (path): Path to a directory.
      * version (str): CIPD instance ID, tag or ref.
    """
    with self.m.context(infra_steps=True):
      pkgs = self.m.cipd.EnsureFile()
      pkgs.add_package('chrome_internal/third_party/sdk/windows', sdk_version)
      self.m.cipd.ensure(sdk_dir, pkgs)
      return sdk_dir

  def _sdk_env(self, sdk_dir, target_arch):
    """Constructs the environment for the SDK.

    Returns environment and environment prefixes.

    Args:
      * sdk_dir (path): Path to a directory containing the SDK.
      * target_arch (str): 'x86' or 'x64'
    """
    env = {}
    env_prefixes = {}

    # Load .../win_sdk/bin/SetEnv.${arch}.json to extract the required
    # environment. It contains a dict that looks like this:
    # {
    #   "env": {
    #     "VAR": [["..", "..", "x"], ["..", "..", "y"]],
    #     ...
    #   }
    # }
    # All these environment variables need to be added to the environment
    # for the compiler and linker to work.
    assert target_arch in ('x86', 'x64')
    filename = 'SetEnv.%s.json' % target_arch
    step_result = self.m.json.read(
        'read %s' % filename, sdk_dir.join('win_sdk', 'bin', filename),
        step_test_data=lambda: self.m.json.test_api.output({
            'env': {
                'PATH': [['..', '..', 'win_sdk', 'bin', 'x64']],
                'VSINSTALLDIR': [['..', '..\\']],
            },
        }))
    data = step_result.json.output.get('env')
    for key in data:
      # recipes' Path() does not like .., ., \, or /, so this is cumbersome.
      # What we want to do is:
      #   [sdk_bin_dir.join(*e) for e in env[k]]
      # Instead do that badly, and rely (but verify) on the fact that the paths
      # are all specified relative to the root, but specified relative to
      # win_sdk/bin (i.e. everything starts with "../../".)
      results = []
      for value in data[key]:
        assert value[0] == '..' and (value[1] == '..' or value[1] == '..\\')
        results.append('%s' % sdk_dir.join(*value[2:]))

      # PATH is special-cased because we don't want to overwrite other things
      # like C:\Windows\System32. Others are replacements because prepending
      # doesn't necessarily makes sense, like VSINSTALLDIR.
      if key.lower() == 'path':
        env_prefixes[key] = results
      else:
        env[key] = ';'.join(results)

    return {'env': env, 'env_prefixes': env_prefixes}
