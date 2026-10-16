from odoo import api, models


class AccountPartialReconcile(models.Model):
    _inherit = 'account.partial.reconcile'

    @api.model_create_multi
    def create(self, vals_list):
        partials = super().create(vals_list)
        self.env['cc.commission.line'].sudo()._create_from_partials(partials)
        return partials

    def unlink(self):
        # Must run before the rows disappear: reverse the commission earned on these collections.
        self.env['cc.commission.line'].sudo()._reverse_for_partials(self.ids)
        return super().unlink()
