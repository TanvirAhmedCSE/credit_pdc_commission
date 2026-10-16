from . import models
from . import wizard
from . import controllers
from . import report

from odoo import Command


def post_init_hook(env):
    """ Make the admin user a Credit & PDC manager. """
    group = env.ref('credit_pdc_commission.group_cc_manager')
    for xmlid in ('base.user_admin', 'base.user_root'):
        user = env.ref(xmlid, raise_if_not_found=False)
        if user:
            field = 'group_ids' if 'group_ids' in user._fields else 'groups_id'
            user.write({field: [Command.link(group.id)]})
