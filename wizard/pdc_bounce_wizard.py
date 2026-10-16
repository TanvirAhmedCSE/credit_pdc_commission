from odoo import fields, models


class CcPdcBounceWizard(models.TransientModel):
    _name = 'cc.pdc.bounce.wizard'
    _description = 'Cheque Bounce'

    pdc_id = fields.Many2one('cc.pdc', required=True, readonly=True)
    currency_id = fields.Many2one(related='pdc_id.currency_id')
    reason = fields.Text("Bounce Reason", required=True)
    bounce_fee = fields.Monetary("Bounce Charge", help="Optional: invoice a bounce charge to the customer.")

    def action_confirm(self):
        self.ensure_one()
        self.pdc_id._do_bounce(self.reason, self.bounce_fee)
        return {'type': 'ir.actions.act_window_close'}
