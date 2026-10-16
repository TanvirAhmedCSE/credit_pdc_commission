from odoo import api, fields, models


class ReportCustomerStatement(models.AbstractModel):
    _name = 'report.credit_pdc_commission.report_customer_statement'
    _description = 'Customer Credit Statement'

    @api.model
    def _get_report_values(self, docids, data=None):
        partners = self.env['res.partner'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'res.partner',
            'docs': partners,
            'data': {p.id: p._cc_statement_data() for p in partners},
            'today': fields.Date.context_today(self),
        }
