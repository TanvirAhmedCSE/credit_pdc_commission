from odoo import fields, models


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    cc_pdc_id = fields.Many2one('cc.pdc', string="Post-Dated Cheque", copy=False,
                                readonly=True, index='btree_not_null', ondelete='set null')

    def _cc_reconcile_invoices(self, invoices):
        """ Reconcile the (posted) payment with the given customer invoices. """
        self.ensure_one()
        pay_lines = self.move_id.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
        for account in pay_lines.account_id:
            inv_lines = invoices.line_ids.filtered(
                lambda l: l.account_id == account and not l.reconciled and l.parent_state == 'posted')
            if inv_lines:
                (pay_lines.filtered(lambda l: l.account_id == account) + inv_lines).reconcile()
