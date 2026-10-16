from markupsafe import Markup
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_amount

from .utils import users_in_group


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    cc_approval_state = fields.Selection(
        [('none', "Not Requested"), ('requested', "Requested"),
         ('approved', "Approved"), ('rejected', "Rejected")],
        string="Credit Approval", default='none', copy=False, tracking=True)
    cc_approved_by = fields.Many2one('res.users', string="Credit Approved By", copy=False, readonly=True)
    cc_approved_amount = fields.Monetary("Approved Amount", copy=False, readonly=True)
    cc_needs_approval = fields.Boolean(compute='_compute_cc_credit')
    cc_credit_message = fields.Text(compute='_compute_cc_credit')

    
    # Credit logic
    
    def _cc_uninvoiced_amount(self):
        """ Order value not yet covered by posted invoices (credit notes reduce the invoiced part). """
        self.ensure_one()
        invoiced = 0.0
        for move in self.invoice_ids.filtered(lambda m: m.state == 'posted'):
            invoiced += -move.amount_total if move.move_type == 'out_refund' else move.amount_total
        return max(self.amount_total - invoiced, 0.0)

    def _cc_get_check(self):
        """ Returns (blocked, message). """
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        if not partner or not (partner.cc_credit_check or partner.cc_credit_hold):
            return False, False
        if partner.cc_credit_hold:
            return True, self.env._(
                "%s is on credit hold. A credit manager must approve this order.", partner.display_name)
        company = self.company_id or self.env.company
        data = partner._cc_credit_data()
        order_amount = self.currency_id._convert(
            self.amount_total, company.currency_id, company, fields.Date.context_today(self))
        if data['exposure'] + order_amount <= partner.cc_credit_limit:
            return False, False
        currency = company.currency_id
        return True, self.env._(
            "Credit limit exceeded for %(partner)s: limit %(limit)s, current exposure %(exposure)s, "
            "this order %(order)s.",
            partner=partner.display_name,
            limit=format_amount(self.env, partner.cc_credit_limit, currency),
            exposure=format_amount(self.env, data['exposure'], currency),
            order=format_amount(self.env, order_amount, currency))

    def _cc_approval_valid(self):
        self.ensure_one()
        return self.cc_approval_state == 'approved' and self.amount_total <= self.cc_approved_amount

    @api.depends('amount_total', 'partner_id', 'state', 'cc_approval_state', 'cc_approved_amount')
    @api.depends_context('company')
    def _compute_cc_credit(self):
        for order in self:
            needs, message = False, False
            if order.state in ('draft', 'sent') and order.partner_id:
                blocked, text = order._cc_get_check()
                needs = blocked and not order._cc_approval_valid()
                message = text if needs else False
            order.cc_needs_approval = needs
            order.cc_credit_message = message

    
    # Confirmation guard
    
    def action_confirm(self):
        if not self.env.context.get('cc_skip_credit_check'):
            for order in self:
                blocked, message = order._cc_get_check()
                if blocked and not order._cc_approval_valid():
                    raise UserError(message + "\n\n" + self.env._(
                        "Use 'Request Credit Approval' so a credit manager can release this order."))
        return super().action_confirm()

    
    # Approval workflow
    
    def action_cc_request_approval(self):
        managers = users_in_group(self.env, 'credit_pdc_commission.group_cc_manager')
        for order in self:
            blocked, message = order._cc_get_check()
            if not blocked:
                raise UserError(self.env._("%s does not need a credit approval.", order.name))
            order.cc_approval_state = 'requested'
            for manager in managers:
                order.activity_schedule(
                    'mail.mail_activity_data_todo', user_id=manager.id,
                    summary=self.env._("Credit approval: %s", order.name), note=message)
            order.message_post(body=Markup("%s<br/>%s") % (self.env._("Credit approval requested."), message))
        return True

    def action_cc_approve(self):
        if not self.env.user.has_group('credit_pdc_commission.group_cc_manager'):
            raise UserError(self.env._("Only a Credit & PDC manager can approve credit overrides."))
        for order in self:
            if order.cc_approval_state != 'requested':
                continue
            order.sudo().write({
                'cc_approval_state': 'approved',
                'cc_approved_by': self.env.user.id,
                'cc_approved_amount': order.amount_total,
            })
            order.activity_feedback(['mail.mail_activity_data_todo'])
            order.message_post(
                body=self.env._("Credit override approved by %s.", self.env.user.name),
                partner_ids=order.user_id.partner_id.ids)
        return True

    def action_cc_reject(self):
        if not self.env.user.has_group('credit_pdc_commission.group_cc_manager'):
            raise UserError(self.env._("Only a Credit & PDC manager can reject credit overrides."))
        for order in self:
            if order.cc_approval_state != 'requested':
                continue
            order.sudo().write({'cc_approval_state': 'rejected'})
            order.activity_feedback(['mail.mail_activity_data_todo'])
            order.message_post(
                body=self.env._("Credit override rejected by %s.", self.env.user.name),
                partner_ids=order.user_id.partner_id.ids)
        return True
