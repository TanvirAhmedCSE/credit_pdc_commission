import logging
from datetime import timedelta

from odoo import api, fields, models

from .utils import PARAM

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    cc_pdc_ids = fields.Many2many(
        'cc.pdc', 'cc_pdc_invoice_rel', 'move_id', 'pdc_id', string="Post-Dated Cheques", copy=False)
    cc_pdc_count = fields.Integer(compute='_compute_cc_pdc')
    cc_has_pdc_in_hand = fields.Boolean(compute='_compute_cc_pdc')
    cc_bounced_count = fields.Integer(compute='_compute_cc_pdc')
    cc_last_reminder_date = fields.Date("Last Overdue Reminder", copy=False)
    cc_days_overdue = fields.Integer("Days Overdue", compute='_compute_cc_days_overdue')

    @api.depends('cc_pdc_ids.state')
    def _compute_cc_pdc(self):
        for move in self:
            pdcs = move.sudo().cc_pdc_ids
            move.cc_pdc_count = len(pdcs)
            move.cc_has_pdc_in_hand = any(p.state in ('received', 'deposited') for p in pdcs)
            move.cc_bounced_count = len(pdcs.filtered(lambda p: p.state == 'bounced'))

    @api.depends('invoice_date_due', 'payment_state', 'state')
    def _compute_cc_days_overdue(self):
        today = fields.Date.context_today(self)
        for move in self:
            overdue = (move.move_type == 'out_invoice' and move.state == 'posted'
                       and move.payment_state in ('not_paid', 'partial') and move.invoice_date_due)
            move.cc_days_overdue = max((today - move.invoice_date_due).days, 0) if overdue else 0

    def action_view_cc_pdc(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Post-Dated Cheques"),
            'res_model': 'cc.pdc',
            'view_mode': 'list,form',
            'domain': [('invoice_ids', 'in', self.ids)],
        }

    @api.model
    def _cron_cc_overdue_reminders(self):
        """ Daily: remind customers of overdue invoices (skipped while a cheque is in hand). """
        icp = self.env['ir.config_parameter'].sudo()
        interval = int(icp.get_param(PARAM + 'reminder_interval_days', 7) or 0)
        template = self.env.ref('credit_pdc_commission.mail_template_overdue_reminder', raise_if_not_found=False)
        if not template or interval <= 0:
            return
        today = fields.Date.context_today(self)
        moves = self.search([
            ('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')), ('invoice_date_due', '<', today),
            '|', ('cc_last_reminder_date', '=', False),
            ('cc_last_reminder_date', '<=', today - timedelta(days=interval)),
        ], limit=100)
        for move in moves:
            if move.cc_has_pdc_in_hand or not move.partner_id.email:
                continue
            try:
                with self.env.cr.savepoint():
                    template.send_mail(move.id, force_send=True,
                                       email_layout_xmlid='mail.mail_notification_light')
                    move.cc_last_reminder_date = today
                    move.message_post(body=self.env._("Overdue payment reminder sent to the customer."))
            except Exception:
                _logger.exception("Overdue reminder failed for invoice %s", move.id)
