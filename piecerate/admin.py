from django.contrib import admin

from .models import RateCardOperation, Style


@admin.register(Style)
class StyleAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(RateCardOperation)
class RateCardOperationAdmin(admin.ModelAdmin):
    list_display = ("style", "op_code", "section", "name", "machine", "rate", "pay_type")
    list_filter = ("style", "pay_type")
    search_fields = ("name",)
