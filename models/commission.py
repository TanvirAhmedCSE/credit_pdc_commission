from odoo import api, fields, models

from .utils import PARAM


class CcCommissionRule(models.Model):
    _name = 'cc.commission.rule'
    _description = 'Commission Rule'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    user_id = fields.Many2one('res.users', "Salesperson", help="Leave empty to apply to every salesperson.")
    rate = fields.Float("Rate (%)", required=True)
    max_days = fields.Integer(
        "Collected Within (days)",
        help="Rule applies only when the money is collected within this many days of the invoice date. "
             "0 = no limit. Use it to reward fast collection.")

    @api.model
    def _get_rate(self, user, days):
        for rule in self.search([]):
            if rule.user_id and rule.user_id != user:
                continue
            if rule.max_days and days > rule.max_days:
                continue
            return rule.rate
        return float(self.env['ir.config_parameter'].sudo().get_param(PARAM + 'default_commission_rate', 2.0))


class CcCommissionLine(models.Model):
    _name = 'cc.commission.line'
    _description = 'Collection Commission'
    _order = 'date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    state = fields.Selection(
        [('draft', "To Approve"), ('approved', "Approved"), ('paid', "Paid"), ('cancel', "Cancelled")],
        default='draft', required=True, index=True, copy=False)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    user_id = fields.Many2one('res.users', "Salesperson", required=True, index=True)
    partner_id = fields.Many2one('res.partner', "Customer", index=True)
    invoice_id = fields.Many2one('account.move', "Invoice", index=True, ondelete='set null')
    payment_move_id = fields.Many2one('account.move', "Collection Entry", ondelete='set null')
    partial_id = fields.Many2one('account.partial.reconcile', index='btree_not_null', ondelete='set null')
    date = fields.Date("Collection Date", required=True, index=True)
    days_to_collect = fields.Integer("Days to Collect")
    collected_amount = fields.Monetary("Collected")
    commission_base = fields.Monetary("Commissionable (excl. tax)")
    rate = fields.Float("Rate (%)")
    amount = fields.Monetary("Commission")
    is_reversal = fields.Boolean(copy=False)
    reversal_of_id = fields.Many2one('cc.commission.line', copy=False, ondelete='set null')
    approved_date = fields.Date(readonly=True, copy=False)
    paid_date = fields.Date(readonly=True, copy=False)
    note = fields.Char()

    @api.depends('invoice_id.name', 'user_id.name', 'is_reversal')
    def _compute_name(self):
        for line in self:
            line.name = "%s%s / %s" % ("[REV] " if line.is_reversal else "",
                                       line.invoice_id.name or '', line.user_id.name or '')

    
    # Accrual on collection
    
    @api.model
    def _create_from_partials(self, partials):
        """ One commission line per partial reconciliation between an invoice and a real payment. """
        Rule = self.env['cc.commission.rule']
        vals_list = []
        for partial in partials:
            invoice = partial.debit_move_id.move_id
            pay_line = partial.credit_move_id
            pay_move = pay_line.move_id
            if invoice.move_type != 'out_invoice' or invoice.state != 'posted':
                continue
            if not invoice.invoice_user_id or not invoice.amount_total:
                continue
            is_collection = pay_move.move_type == 'entry' and bool(
                getattr(pay_move, 'origin_payment_id', False) or getattr(pay_move, 'statement_line_id', False))
            if not is_collection:
                continue  # credit notes, exchange differences, etc. are not collections
            currency = partial.company_id.currency_id
            base = currency.round(partial.amount * invoice.amount_untaxed / invoice.amount_total)
            days = (pay_line.date - invoice.invoice_date).days if invoice.invoice_date else 0
            rate = Rule._get_rate(invoice.invoice_user_id, days)
            vals_list.append({
                'company_id': partial.company_id.id,
                'user_id': invoice.invoice_user_id.id,
                'partner_id': invoice.commercial_partner_id.id,
                'invoice_id': invoice.id,
                'payment_move_id': pay_move.id,
                'partial_id': partial.id,
                'date': pay_line.date,
                'days_to_collect': days,
                'collected_amount': partial.amount,
                'commission_base': base,
                'rate': rate,
                'amount': currency.round(base * rate / 100.0),
            })
        return self.create(vals_list) if vals_list else self.browse()

    @api.model
    def _reverse_for_partials(self, partial_ids):
        """ Collection undone (cheque bounced, payment unreconciled): cancel or counter-book the commission. """
        lines = self.search([('partial_id', 'in', partial_ids), ('state', '!=', 'cancel'),
                             ('is_reversal', '=', False)])
        reversals = []
        for line in lines:
            if line.state == 'paid':
                reversals.append({
                    'company_id': line.company_id.id, 'user_id': line.user_id.id,
                    'partner_id': line.partner_id.id, 'invoice_id': line.invoice_id.id,
                    'date': fields.Date.context_today(self), 'days_to_collect': line.days_to_collect,
                    'collected_amount': -line.collected_amount, 'commission_base': -line.commission_base,
                    'rate': line.rate, 'amount': -line.amount, 'is_reversal': True,
                    'reversal_of_id': line.id, 'note': self.env._("Collection reversed after payout"),
                })
            else:
                line.state = 'cancel'
        if reversals:
            self.create(reversals)

    
    # Workflow
    
    def action_approve(self):
        today = fields.Date.context_today(self)
        self.filtered(lambda l: l.state == 'draft').write({'state': 'approved', 'approved_date': today})

    def action_pay(self):
        today = fields.Date.context_today(self)
        self.filtered(lambda l: l.state == 'approved').write({'state': 'paid', 'paid_date': today})

    def action_draft(self):
        self.filtered(lambda l: l.state in ('approved', 'cancel')).write(
            {'state': 'draft', 'approved_date': False})
