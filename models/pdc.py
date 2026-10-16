import logging

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .utils import PARAM

_logger = logging.getLogger(__name__)


class CcPdc(models.Model):
    _name = 'cc.pdc'
    _description = 'Post-Dated Cheque'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'cheque_date desc, id desc'

    def _default_journal(self):
        journal_id = int(self.env['ir.config_parameter'].sudo().get_param(PARAM + 'pdc_journal_id', 0) or 0)
        return self.env['account.journal'].browse(journal_id).exists().id or False

    name = fields.Char("Reference", default='New', readonly=True, copy=False)
    state = fields.Selection([
        ('received', "Received"), ('deposited', "Deposited"), ('cleared', "Cleared"),
        ('bounced', "Bounced"), ('cancel', "Cancelled"),
    ], default='received', required=True, tracking=True, copy=False, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    partner_id = fields.Many2one('res.partner', "Customer", required=True, tracking=True, index=True)
    commercial_partner_id = fields.Many2one(related='partner_id.commercial_partner_id', store=True, index=True)
    cheque_no = fields.Char("Cheque No.", required=True, tracking=True)
    bank_name = fields.Char("Drawee Bank", required=True)
    received_date = fields.Date(default=fields.Date.context_today, required=True)
    cheque_date = fields.Date("Cheque Date", required=True, tracking=True, index=True,
                              help="The post-dated date written on the cheque.")
    amount = fields.Monetary(required=True, tracking=True)
    invoice_ids = fields.Many2many(
        'account.move', 'cc_pdc_invoice_rel', 'pdc_id', 'move_id', string="Invoices", copy=False)
    invoice_due = fields.Monetary("Invoices Due", compute='_compute_invoice_due')
    deposit_journal_id = fields.Many2one(
        'account.journal', "Bank Journal", default=_default_journal, tracking=True,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]")
    deposit_date = fields.Date(readonly=True, copy=False)
    clear_date = fields.Date(readonly=True, copy=False)
    bounce_date = fields.Date(readonly=True, copy=False)
    bounce_reason = fields.Text(readonly=True, copy=False)
    payment_id = fields.Many2one('account.payment', "Payment", readonly=True, copy=False, ondelete='set null')
    user_id = fields.Many2one('res.users', "Collected By", default=lambda self: self.env.user, tracking=True)
    days_to_maturity = fields.Integer(compute='_compute_days_to_maturity')
    maturity_notified = fields.Boolean(copy=False)
    note = fields.Text()

    
    # Computes / constraints
    
    @api.depends('invoice_ids.amount_residual')
    def _compute_invoice_due(self):
        for pdc in self:
            pdc.invoice_due = sum(pdc.invoice_ids.mapped('amount_residual'))

    @api.depends('cheque_date')
    def _compute_days_to_maturity(self):
        today = fields.Date.context_today(self)
        for pdc in self:
            pdc.days_to_maturity = (pdc.cheque_date - today).days if pdc.cheque_date else 0

    @api.onchange('invoice_ids')
    def _onchange_invoice_ids(self):
        if self.invoice_ids and not self.amount:
            self.amount = sum(self.invoice_ids.mapped('amount_residual'))

    @api.constrains('amount')
    def _check_amount(self):
        if any(pdc.amount <= 0 for pdc in self):
            raise ValidationError(self.env._("The cheque amount must be greater than zero."))

    @api.constrains('invoice_ids', 'partner_id')
    def _check_invoices(self):
        for pdc in self:
            other = pdc.invoice_ids.filtered(
                lambda m: m.commercial_partner_id != pdc.partner_id.commercial_partner_id)
            if other:
                raise ValidationError(self.env._("All invoices must belong to the cheque's customer."))

    @api.constrains('cheque_no', 'bank_name', 'partner_id', 'state')
    def _check_duplicate(self):
        for pdc in self:
            if pdc.state == 'cancel':
                continue
            if self.search_count([('id', '!=', pdc.id), ('cheque_no', '=', pdc.cheque_no),
                                  ('bank_name', '=ilike', pdc.bank_name),
                                  ('partner_id', '=', pdc.partner_id.id), ('state', '!=', 'cancel')]):
                raise ValidationError(self.env._(
                    "Cheque %(no)s of %(bank)s is already registered for this customer.",
                    no=pdc.cheque_no, bank=pdc.bank_name))

    
    # ORM
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('cc.pdc') or 'New'
        records = super().create(vals_list)
        for pdc in records:
            pdc._send_template('credit_pdc_commission.mail_template_pdc_received')
        return records

    def unlink(self):
        if any(pdc.state not in ('received', 'cancel') for pdc in self):
            raise UserError(self.env._("Only received or cancelled cheques can be deleted."))
        return super().unlink()

    
    # Helpers
    
    def _send_template(self, xmlid):
        self.ensure_one()
        template = self.env.ref(xmlid, raise_if_not_found=False)
        if not template or not self.partner_id.email:
            return False
        try:
            with self.env.cr.savepoint():
                template.sudo().send_mail(self.id, force_send=True,
                                          email_layout_xmlid='mail.mail_notification_light')
        except Exception:
            _logger.exception("PDC email failed for %s", self.id)
            return False
        return True

    def _create_bounce_invoice(self, fee):
        self.ensure_one()
        today = fields.Date.context_today(self)
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'invoice_date': today,
            'invoice_date_due': today,
            'invoice_user_id': False,  # no sales commission on bounce charges
            'ref': self.env._("Cheque bounce charge %s", self.name),
            'invoice_line_ids': [Command.create({
                'name': self.env._("Cheque bounce charge - %(no)s (%(bank)s)",
                                   no=self.cheque_no, bank=self.bank_name),
                'quantity': 1, 'price_unit': fee})],
        })
        move.action_post()
        return move

    
    # Workflow
    
    def action_deposit(self):
        today = fields.Date.context_today(self)
        for pdc in self:
            if pdc.state != 'received':
                raise UserError(self.env._("Only received cheques can be deposited."))
            if pdc.cheque_date > today:
                raise UserError(self.env._(
                    "Cheque %(no)s is post-dated to %(date)s and cannot be deposited yet.",
                    no=pdc.cheque_no, date=pdc.cheque_date))
            if not pdc.deposit_journal_id:
                raise UserError(self.env._("Select the bank journal before depositing the cheque."))
            pdc.write({'state': 'deposited', 'deposit_date': today})
            pdc.message_post(body=self.env._("Cheque deposited to %s.", pdc.deposit_journal_id.display_name))
        return True

    def action_clear(self):
        today = fields.Date.context_today(self)
        for pdc in self:
            if pdc.state != 'deposited':
                raise UserError(self.env._("Only deposited cheques can be marked as cleared."))
            invoices = pdc.invoice_ids.filtered(
                lambda m: m.state == 'posted' and m.payment_state in ('not_paid', 'partial'))
            payment = self.env['account.payment'].create({
                'payment_type': 'inbound',
                'partner_type': 'customer',
                'partner_id': pdc.partner_id.id,
                'amount': pdc.amount,
                'currency_id': pdc.currency_id.id,
                'journal_id': pdc.deposit_journal_id.id,
                'date': today,
                'memo': "%s / Chq %s (%s)" % (pdc.name, pdc.cheque_no, pdc.bank_name),
                'cc_pdc_id': pdc.id,
            })
            payment.action_post()
            payment._cc_reconcile_invoices(invoices)
            pdc.write({'state': 'cleared', 'clear_date': today, 'payment_id': payment.id})
            pdc.message_post(body=self.env._("Cheque cleared. Payment %s posted and invoices reconciled.",
                                             payment.display_name))
        return True

    def action_open_bounce(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Cheque Bounced"),
            'res_model': 'cc.pdc.bounce.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_pdc_id': self.id},
        }

    def _do_bounce(self, reason, fee=0.0):
        today = fields.Date.context_today(self)
        threshold = int(self.env['ir.config_parameter'].sudo().get_param(PARAM + 'hold_after_bounces', 0) or 0)
        for pdc in self:
            if pdc.state not in ('received', 'deposited', 'cleared'):
                raise UserError(self.env._("This cheque cannot be marked as bounced."))
            if pdc.state == 'cleared' and pdc.payment_id:
                # Undo the collection: unreconciles the invoices, so they become due/overdue again.
                payment = pdc.payment_id
                if payment.state != 'draft':
                    payment.action_draft()
                payment.action_cancel()
            pdc.write({'state': 'bounced', 'bounce_date': today, 'bounce_reason': reason})
            invoices = pdc.invoice_ids
            invoices.sudo().write({'cc_last_reminder_date': False})  # reminder cron picks them up again
            pdc.message_post(body=self.env._("Cheque bounced. Reason: %s", reason))
            if fee:
                charge = pdc._create_bounce_invoice(fee)
                pdc.message_post(body=self.env._("Bounce charge invoice %s created.", charge.name))
            for user in (invoices.invoice_user_id | pdc.user_id):
                pdc.activity_schedule(
                    'mail.mail_activity_data_todo', user_id=user.id,
                    summary=self.env._("Bounced cheque %(no)s - %(partner)s",
                                       no=pdc.cheque_no, partner=pdc.partner_id.name),
                    note=reason)
            pdc._send_template('credit_pdc_commission.mail_template_pdc_bounced')
            commercial = pdc.commercial_partner_id
            if threshold and not commercial.cc_credit_hold:
                bounced = self.search_count([('commercial_partner_id', '=', commercial.id),
                                             ('state', '=', 'bounced')])
                if bounced >= threshold:
                    commercial.sudo().write({'cc_credit_hold': True})
                    commercial.sudo().message_post(body=self.env._(
                        "Credit hold applied automatically after %s bounced cheques.", bounced))
        return True

    def action_cancel(self):
        for pdc in self:
            if pdc.state not in ('received', 'deposited'):
                raise UserError(self.env._("Only received or deposited cheques can be cancelled."))
        self.write({'state': 'cancel'})

    def action_reset(self):
        self.filtered(lambda p: p.state == 'cancel').write({'state': 'received', 'maturity_notified': False})

    def action_send_receipt(self):
        for pdc in self:
            if not pdc._send_template('credit_pdc_commission.mail_template_pdc_received'):
                raise UserError(self.env._("Could not send the email. Check the customer's email address "
                                           "and the outgoing mail server."))

    def action_view_payment(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.payment',
                'res_id': self.payment_id.id, 'view_mode': 'form'}

    
    # Cron
    
    @api.model
    def _cron_maturity_alerts(self):
        """ Daily: tell the collector that a post-dated cheque can now be deposited. """
        today = fields.Date.context_today(self)
        admin = self.env.ref('base.user_admin')
        for pdc in self.search([('state', '=', 'received'), ('cheque_date', '<=', today),
                                ('maturity_notified', '=', False)]):
            try:
                with self.env.cr.savepoint():
                    pdc.activity_schedule(
                        'mail.mail_activity_data_todo', date_deadline=today,
                        user_id=(pdc.user_id or admin).id,
                        summary=self.env._("Deposit cheque %(no)s - %(partner)s",
                                           no=pdc.cheque_no, partner=pdc.partner_id.name))
                    pdc.maturity_notified = True
            except Exception:
                _logger.exception("Maturity alert failed for PDC %s", pdc.id)
