from django.urls import path

from . import views

urlpatterns = [
    path("", views.piece_rate_view, name="piece_rate"),
    path("templates/", views.template_list_view, name="piece_rate_templates"),
    path("operations/", views.master_operations_view, name="piece_rate_operations"),
    path("operators/", views.operator_summary_view, name="piece_rate_operator_summary"),
    path("styles-summary/", views.style_summary_view, name="piece_rate_style_summary"),
    path("management-summary/", views.management_summary_view, name="piece_rate_management_summary"),
    path("production/", views.production_shortcut_view, name="piece_rate_production_shortcut"),
    path("styles/<int:style_id>/", views.rate_card_view, name="piece_rate_rate_card"),
    path("styles/<int:style_id>/production/", views.production_view, name="piece_rate_production"),
    path("operator-links/", views.operator_links_view, name="piece_rate_operator_links"),
    path("entry/<str:token>/", views.operator_entry_view, name="piece_rate_operator_entry"),
    path("entry/<str:token>/history/<int:rc_op_id>/", views.operator_history_view, name="piece_rate_operator_history"),
]
