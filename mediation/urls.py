from django.urls import path
from . import views

app_name = 'mediation'

urlpatterns = [
    path('', views.mediation_list, name='index'),
    path('<int:mediation_id>/', views.mediation_detail, name='mediation_detail'),
    path('<int:mediation_id>/detail/', views.mediation_detail, name='detail'),
]

