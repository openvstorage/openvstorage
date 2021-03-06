# Copyright (C) 2019 iNuron NV
#
# This file is part of Open vStorage Open Source Edition (OSE),
# as available from
#
#      http://www.openvstorage.org and
#      http://www.openvstorage.com.
#
# This file is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License v3 (GNU AGPLv3)
# as published by the Free Software Foundation, in version 3 as it comes
# in the LICENSE.txt file of the Open vStorage OSE distribution.
#
# Open vStorage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY of any kind.

"""
Not something that we're proud of but it has to be this way :(
The unittest do not require any implementation to run, everything gets mocked
However when loading in all other commands, the imports might/do fetch instances of real implementation
Which don't do anything or cannot be instantiated
Thus we have to import controllers whenever we invoke a command :(
"""
from __future__ import absolute_import

import click
import logging.config
from .setup import setup_group
from .config import config_group
from .misc import collect_logs, version_command
from .remove import remove_group
from .monitor import monitor_group
from .services import framework_start, framework_stop
from .update import update_command
from .rollback import rollback_command
from .unittesting import unittest_command
from .local_update import local_update_group
from ovs_extensions.cli import OVSCLI
from IPython import embed


@click.group(name='ovs', help='Open the OVS python shell or run an ovs command', invoke_without_command=True, cls=OVSCLI)
@click.pass_context
def ovs(ctx):
    # @todo configure logging for both file and console
    if ctx.invoked_subcommand is None:
        # Configuring logging is not necessary here. It will invoke the ipython shell which is configured.
        from ovs.extensions.log import get_log_config_shells
        logging.config.dictConfig(get_log_config_shells())
        embed()
    # Documentation purposes:
    # else:
    # Do nothing: invoke subcommand


groups = [setup_group,
          config_group,
          rollback_command,
          update_command,
          remove_group,
          monitor_group,
          unittest_command,
          framework_start, framework_stop,
          collect_logs, version_command,
          local_update_group]
for group in groups:
    ovs.add_command(group)
