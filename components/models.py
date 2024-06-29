from django.db import models
from polymorphic.models import PolymorphicModel
from dataclasses import dataclass


@dataclass(init=False)
# Create your models here.
class Address(models.Model):
    full_address = models.CharField(max_length=255)


@dataclass(init=False)
class FinancialInstitution(models.Model):
    full_name = models.CharField(max_length=255)
    abbreviation = models.CharField(max_length=5)
    address = models.ForeignKey(Address, on_delete=models.DO_NOTHING)
    company_registration_number = models.CharField(max_length=20)
    gst_registration_number = models.CharField(max_length=20)
    email = models.EmailField(max_length=255)


@dataclass(init=False)
class InstrumentHolder(models.Model):
    full_name = models.CharField(max_length=255)
    address = models.ForeignKey(Address, on_delete=models.DO_NOTHING)


@dataclass(init=False)
class Instrument(PolymorphicModel):
    name = models.CharField(max_length=255)
    holder = models.ForeignKey(InstrumentHolder, on_delete=models.DO_NOTHING)
    provider = models.ForeignKey(FinancialInstitution, on_delete=models.DO_NOTHING)
    number = models.CharField(max_length=20)
    currency = models.CharField(max_length=3)


@dataclass(init=False)
class Account(Instrument):
    type = models.CharField(max_length=10)
    credit_line = models.DecimalField(max_digits=20, decimal_places=2)
    balance = models.DecimalField(max_digits=20, decimal_places=2)


@dataclass(init=False)
class Card(Instrument):
    total_credit_limit = models.PositiveIntegerField('total credit limit')


@dataclass(init=False)
class Transaction(PolymorphicModel):
    instrument = models.ForeignKey(Instrument, on_delete=models.DO_NOTHING)
    date = models.DateField('transaction date', null=True)
    description = models.CharField(max_length=255)
    sub_description = models.CharField(max_length=500)
    amount = models.DecimalField(max_digits=20, decimal_places=2)


@dataclass(init=False)
class AccountTransaction(Transaction):
    # withdrawals are considered transaction amounts
    deposits = models.DecimalField(max_digits=20, decimal_places=2)
    balance = models.DecimalField(max_digits=20, decimal_places=2)


@dataclass(init=False)
class CardTransaction(Transaction):
    # transaction date is the date used for base transactions
    post_date = models.DateField('post date', null=True)
    cash_rebate = models.DecimalField(max_digits=20, decimal_places=2)
