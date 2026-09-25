from django.urls import path
from . import views

app_name = 'cases'

urlpatterns = [
    path('', views.index, name='index'),
    path('apply/', views.application_create, name='application_create'),
    path('applications/<str:application_id>/', views.application_detail, name='application_detail'),
    path('udc/intake/', views.udc_intake, name='udc_intake'),
    path('udc/confirmation/<str:application_id>/', views.udc_confirmation, name='udc_confirmation'),
    path('officer/application/<str:application_id>/', views.officer_application_detail, name='officer_application_detail'),
    path('officer/case/<str:case_id>/', views.officer_case_detail, name='officer_case_detail'),
]
