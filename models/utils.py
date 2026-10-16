from odoo import SUPERUSER_ID

PARAM = 'credit_pdc_commission.'

BUCKETS = [
    ('not_due', "Not Due"),
    ('d1_30', "1-30 Days"),
    ('d31_60', "31-60 Days"),
    ('d61_90', "61-90 Days"),
    ('d90_plus', "90+ Days"),
]


def bucket_for(days):
    if days <= 0:
        return 'not_due'
    if days <= 30:
        return 'd1_30'
    if days <= 60:
        return 'd31_60'
    if days <= 90:
        return 'd61_90'
    return 'd90_plus'


def users_in_group(env, xmlid):
    """ Internal, active users belonging to the group (works on 18/19 field names). """
    group = env.ref(xmlid)
    Users = env['res.users'].sudo()
    field = 'group_ids' if 'group_ids' in Users._fields else 'groups_id'
    return Users.search([(field, 'in', group.id), ('share', '=', False), ('id', '!=', SUPERUSER_ID)])
