from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cc_pdc_journal_id = fields.Many2one(
        'account.journal', "Default PDC Bank Journal",
        domain="[('type', 'in', ('bank', 'cash'))]",
        config_parameter='credit_pdc_commission.pdc_journal_id')
    cc_default_commission_rate = fields.Float(
        "Default Commission Rate (%)",
        config_parameter='credit_pdc_commission.default_commission_rate', default=2.0)
    cc_reminder_interval_days = fields.Integer(
        "Overdue Reminder Every (days)",
        config_parameter='credit_pdc_commission.reminder_interval_days', default=7)
    cc_hold_after_bounces = fields.Integer(
        "Auto Credit Hold After N Bounced Cheques",
        config_parameter='credit_pdc_commission.hold_after_bounces', default=0)
