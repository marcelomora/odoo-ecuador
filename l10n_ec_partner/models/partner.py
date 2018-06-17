# -*- coding: utf-8 -*-

import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)
try:
    from stdnum import ec
except ImportError as err:
    _logger.debug('Cannot import stdnum')


class ResPartner(models.Model):

    _inherit = 'res.partner'

    @api.multi
    @api.depends('vat', 'name')
    def name_get(self):
        """Name get method."""
        data = []
        for partner in self:
            display_val = u'[{0}]{1}'.format(
                partner.vat and partner.vat[2:] or '*',
                partner.name
            )
            data.append((partner.id, display_val))
        return data

    @api.multi
    @api.depends('vat', 'name')
    def _compute_display_name(self):
        """Name get method."""
        self.ensure_one()
        self.display_name = u'[{0}]{1}'.format(
            self.vat and self.vat[2:] or '*',
            self.name
        )

    @api.model
    def name_search(self, name, args=None, operator='ilike', limit=80):
        if not args:
            args = []
        if name:
            partners = self.search([('vat', operator, name)] + args, limit=limit)  # noqa
            if not partners:
                partners = self.search([('name', operator, name)] + args, limit=limit)  # noqa
        else:
            partners = self.search(args, limit=limit)
        return partners.name_get()

    @api.multi
    @api.constrains('vat')
    def _check_vat(self):
        self.ensure_one()
        val_func_dict = {'citizenship_card': ec.ci.is_valid,
                         'ruc': ec.ruc.is_valid
                         }
        if self.vat_type == 'passport':
            return True

        if val_func_dict[self.vat_type](self.vat[2:]):
            return True
        else:
            raise ValidationError('Error en el identificador.')

    @api.one
    @api.depends('vat')
    def _compute_tipo_persona(self):
        if not self.vat:
            self.tipo_persona = '0'
        elif int(self.vat[4]) <= 6:
            self.tipo_persona = '6'
        elif int(self.vat[4]) in [6, 9]:
            self.tipo_persona = '9'
        else:
            self.tipo_persona = '0'

    identifier = fields.Char(
        'Cedula/ RUC',
        size=13,
        required=False,
        help='Identificación o Registro Unico de Contribuyentes')

    vat_type = fields.Selection(
        [
            ('citizenship_card', 'CEDULA'),
            ('ruc', 'RUC'),
            ('passport', 'PASAPORTE')
        ],
        'Tipo ID',
        required=False,
        default='passport'
    )

    tipo_persona = fields.Selection(
        compute='_compute_tipo_persona',
        selection=[
            ('6', 'Persona Natural'),
            ('9', 'Persona Juridica'),
            ('0', 'Otro')
        ],
        string='Persona',
        store=True
    )

    fantasy_name = fields.Char(string="Fantasy Name", )
    display_name = fields.Char(string="Display Name", compute='_compute_display_name')

    def validate_from_sri(self):
        """Validate from SRI(IRS) site."""
        SRI_LINK = "https://declaraciones.sri.gob.ec/facturacion-internet/consultas/publico/ruc-datos1.jspa"  # noqa
        texto = '0103893954'  # noqa

    _sql_constraints = {
        ('vat_unique', 'UNIQUE(vat)', 'VAT number must be unique!')
    }


class ResCompany(models.Model):
    _inherit = 'res.company'

    accountant_id = fields.Many2one('res.partner', 'Contador')
    sri_id = fields.Many2one('res.partner', 'Servicio de Rentas Internas')
    cedula_rl = fields.Char('Cédula Representante Legal', size=10)
