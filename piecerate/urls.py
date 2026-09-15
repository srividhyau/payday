from django.urls import path

from . import views

urlpatterns = [
    path("", views.piece_rate_view, name="piece_rate"),
    path("templates/", views.template_list_view, name="piece_rate_templates"),
    path("operations/", views.master_operations_view, name="piece_rate_operations"),
    path("styles/<int:style_id>/", views.rate_card_view, name="piece_rate_rate_card"),
]
