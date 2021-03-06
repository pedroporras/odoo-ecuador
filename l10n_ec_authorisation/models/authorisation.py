# -*- coding: utf-8 -*-
# © <2016> <Cristian Salamea>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import time
from datetime import datetime
import re
import logging

from odoo import api, fields, models, _
from odoo.exceptions import (
    ValidationError,
    UserError
)

_logger = logging.getLogger(__name__)


class AccountAtsDoc(models.Model):
    _name = 'account.ats.doc'
    _description = 'Tipos Comprobantes Autorizados'

    code = fields.Char(u'Código', size=2, required=True)
    name = fields.Char('Tipo Comprobante', size=64, required=True)


class AccountAtsSustento(models.Model):
    _name = 'account.ats.sustento'
    _description = 'Sustento del Comprobante'

    @api.multi
    @api.depends('code', 'type')
    def name_get(self):
        res = []
        for record in self:
            name = '%s - %s' % (record.code, record.type)
            res.append((record.id, name))
        return res

    _rec_name = 'type'

    code = fields.Char(u'Código', size=2, required=True)
    type = fields.Char('Tipo de Sustento', size=128, required=True)


class AccountAuthorisation(models.Model):

    _name = 'account.authorisation'
    _order = 'expiration_date desc'

    @api.multi
    @api.depends('type_id', 'num_start', 'num_end')
    def name_get(self):
        res = []
        for record in self:
            if record.is_electronic:
                name = "{} (Electronic)".format(record.type_id.code)
            else:
                name = u'%s (%s-%s)' % (
                    record.type_id.code,
                    record.num_start,
                    record.num_end
                )
            res.append((record.id, name))
        return res

    @api.multi
    @api.depends('is_electronic', 'expiration_date')
    def _compute_active(self):
        """
        Check the due_date to give the value active field
        """
        for auth in self:
            if auth.is_electronic or not auth.expiration_date:
                auth.active = True
                continue

            now = datetime.strptime(time.strftime("%Y-%m-%d"), '%Y-%m-%d')
            due_date = datetime.strptime(auth.expiration_date, '%Y-%m-%d')
            auth.active = now < due_date

    def _get_type(self):
        return self._context.get('type', 'in_invoice')  # pylint: disable=E1101

    def _get_in_type(self):
        return self._context.get('in_type', 'external')

    def _get_partner(self):
        partner = self.env.user.company_id.partner_id
        if self._context.get('partner_id'):
            partner = self._context.get('partner_id')
        return partner

    @api.model
    def create(self, values):
        """Create override."""
        res = self.search([('partner_id', '=', values['partner_id']),
                           ('type_id', '=', values['type_id']),
                           ('entity', '=', values['entity']),
                           ('emission_point', '=', values['emission_point']),
                           ('active', '=', True)])
        if res:
            MSG = u'Ya existe una autorización activa para %s' % res.type_id.name  # noqa
            raise ValidationError(MSG)

        partner_id = self.env.user.company_id.partner_id.id
        if values['partner_id'] == partner_id:
            typ = self.env['account.ats.doc'].browse(values['type_id'])
            name_type = '{0}_{1}'.format(values['name'], values['type_id'])
            # sequence_data = {
            #     'code': typ.code == '07' and 'account.retention' or 'account.invoice',  # noqa
            #     'name': name_type,
            #     'padding': 9,
            #     'number_next': values['num_start'],
            #     }
            # seq = self.env['ir.sequence'].create(sequence_data)
            # values.update({'sequence_id': seq.id})
        return super(AccountAuthorisation, self).create(values)

    @api.multi
    def unlink(self):
        """Don't allow unlik of used authorizations."""
        inv = self.env['account.invoice']
        res = inv.search([('auth_inv_id', '=', self.id)])
        if res:
            raise UserError(
                'This authorization is related to a document.'
            )
        return super(AccountAuthorisation, self).unlink()

    name = fields.Char('Authorization Number', size=128)
    entity = fields.Char('Entity', size=3, required=True)
    emission_point = fields.Char('Emission Point', size=3, required=True)
    num_start = fields.Integer('From')
    num_end = fields.Integer('To')
    is_electronic = fields.Boolean('Is Electronic?')
    expiration_date = fields.Date('Expiration Date')
    active = fields.Boolean(
        compute='_compute_active',
        string='Active',
        store=False,
        default=True
        )
    in_type = fields.Selection(
        [('internal', 'Internal'),
         ('external', 'External')],
        string='Internal Type',
        readonly=True,
        change_default=True,
        default=_get_in_type
        )
    type_id = fields.Many2one(
        'account.ats.doc',
        'Voucher Type',
        required=True
        )
    partner_id = fields.Many2one(
        'res.partner',
        'Parner',
        required=True,
        default=_get_partner
        )
    sequence_id = fields.Many2one(
        'ir.sequence',
        'Sequence',
        help='Secuencia Alfanumerica para el documento, se debe registrar cuando pertenece a la compañia',  # noqa
        ondelete='cascade'
        )

    _sql_constraints = [
        ('number_unique',
         'unique(partner_id,expiration_date,type_id)',
         u'Authorization, expiration date and voucher type must be unique.'),  # noqa
        ]

    def is_valid_number(self, number):
        """Verify if @number is in [@num_start,@num_end] range."""
        if self.num_start <= number <= self.num_end:
            return True
        return False


class ResPartner(models.Model):
    """res.partner inheritance"""
    _inherit = 'res.partner'

    authorisation_ids = fields.One2many(
        'account.authorisation',
        'partner_id',
        'Authorizations'
        )

    @api.multi
    def get_authorisation(self, type_document):
        map_type = {
            'out_invoice': '18',
            'in_invoice': '01',
            'out_refund': '04',
            'in_refund': '05',
            'liq_purchase': '03',
            'ret_in_invoice': '07',
        }
        code = map_type[type_document]
        for a in self.authorisation_ids:
            if a.active and a.type_id.code == code:
                return a
        raise ValidationError(_("Not found authorization for document type {}".format(code))) 


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    auth_out_invoice_id = fields.Many2one(
        'account.authorisation',
        'Secuencia Facturas'
    )
    auth_out_refund_id = fields.Many2one(
        'account.authorisation',
        'Secuencia Notas de Credito'
    )
    auth_retention_id = fields.Many2one(
        'account.authorisation',
        'Para Retenciones'
    )


class AccountInvoice(models.Model):
    _inherit = 'account.invoice'

    _DOCUMENTOS_EMISION = ['out_invoice', 'liq_purchase', 'out_refund']

    # @api.onchange('journal_id', 'auth_inv_id')
    # def _onchange_journal_id(self):
    #     super(AccountInvoice, self)._onchange_journal_id()
    #     if self.journal_id and self.type in self._DOCUMENTOS_EMISION:
    #         if self.type == 'out_invoice':
    #             self.auth_inv_id = self.journal_id.auth_out_invoice_id
    #         elif self.type == 'out_refund':
    #             self.auth_inv_id = self.journal_id.auth_out_refund_id
    #         self.auth_number = not self.auth_inv_id.is_electronic and self.auth_inv_id.name  # noqa
    #         number = '{:09d}'.format(
    #             self.auth_inv_id.sequence_id.number_next_actual
    #         )
    #         self.reference = number

    @api.onchange('partner_id', 'company_id')
    def _onchange_partner_id(self):
        """
        Redefinicion de metodo para obtener:
        numero de autorizacion
        numero de documento
        El campo auth_inv_id representa la autorizacion para
        emitir el documento.
        """
        super(AccountInvoice, self)._onchange_partner_id()
        if self.type not in self._DOCUMENTOS_EMISION:
            self.auth_inv_id = self.partner_id.get_authorisation(self.type)

    @api.one
    @api.depends(
        'state',
        'reference'
    )
    @api.depends('auth_inv_id', 'reference')
    def _compute_invoice_number(self):
        """
        Calcula el numero de factura segun el
        establecimiento seleccionado
        """
        if self.reference:
            self.invoice_number = '{}{}{}'.format(
                self.auth_inv_id.entity,
                self.auth_inv_id.emission_point,
                self.reference
            )
        else:
            self.invoice_number = '*'

    invoice_number = fields.Char(
        compute='_compute_invoice_number',
        string='Nro. Documento',
        store=True,
        readonly=True,
        copy=False
    )
    internal_inv_number = fields.Char('Numero Interno', copy=False)
    auth_inv_id = fields.Many2one(
        'account.authorisation',
        string='Establecimiento',
        readonly=True,
        states={'draft': [('readonly', False)]},
        help='Autorizacion para documento',
        copy=False
    )
    auth_number = fields.Char('Autorización')
    sustento_id = fields.Many2one(
        'account.ats.sustento',
        string='Sustento del Comprobante'
    )

    _sql_constraints = [
        (
            'unique_invoice_number',
            'unique(reference,type,partner_id,state)',
            'Invoice number must be unique.'
        )
    ]

    @api.onchange('auth_inv_id')
    def _onchange_auth(self):
        if self.auth_inv_id and not self.auth_inv_id.is_electronic:
            self.auth_number = self.auth_inv_id.name

    @api.onchange('reference')
    def _onchange_ref(self):
        # TODO: agregar validacion numerica a reference
        if self.reference:
            self.reference = self.reference.zfill(9)
            if not self.auth_inv_id.is_valid_number(int(self.reference)):
                return {
                    'value': {
                        'reference': ''
                    },
                    'warning': {
                        'title': 'Error',
                        'message': u'Número no coincide con la autorización ingresada.'  # noqa
                    }
                }

    @api.constrains('auth_number')
    def check_reference(self):
        """
        Metodo que verifica la longitud de la autorizacion
        10: documento fisico
        35: factura electronica modo online
        49: factura electronica modo offline
        """
        if self.type not in ['in_invoice', 'liq_purchase']:
            return
        if self.auth_number and len(self.auth_number) not in [10, 35, 49]:
            raise UserError(
                u'Debe ingresar 10, 35 o 49 dígitos según el documento.'
            )

    @api.multi
    def action_number(self):
        # TODO: ver donde incluir el metodo de numeracion
        self.ensure_one()
        if self.type not in ['out_invoice', 'liq_purchase', 'out_refund']:
            return
        number = self.internal_inv_number
        if not self.auth_inv_id:
            # asegura la autorizacion en el objeto
            self._onchange_partner_id()
        if not number:
            sequence = self.auth_inv_id.sequence_id
            number = sequence.next_by_id()
        self.write({'reference': number, 'internal_inv_number': number})

    @api.multi
    def action_invoice_open(self):
        res = super(AccountInvoice, self).action_invoice_open()
        for inv in self:
            if inv.journal_id and inv.type in self._DOCUMENTOS_EMISION:
                if inv.type == 'out_invoice':
                    inv.auth_inv_id = inv.journal_id.auth_out_invoice_id
                elif inv.type == 'out_refund':
                    inv.auth_inv_id = inv.journal_id.auth_out_refund_id
                number = inv.auth_inv_id.sequence_id.next_by_id()
                if re.match("\d{9}", number) is None:
                    _logger.info(number)
                    raise ValidationError(_("Sequence must be filled with 9 zeros"))
                inv.reference = number
        return res

