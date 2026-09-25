from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('citizen/', views.citizen_dashboard, name='citizen'),
    path('officer/', views.officer_dashboard, name='officer'),
    path('support-staff/', views.support_staff_dashboard, name='support_staff'),
    path('udc-operator/', views.udc_operator_dashboard, name='udc_operator'),
    path('panel-lawyer/', views.panel_lawyer_dashboard, name='panel_lawyer'),
    path('mediator/', views.mediator_dashboard, name='mediator'),
    path('helpline-agent/', views.helpline_agent_dashboard, name='helpline_agent'),
    path('representative/', views.representative_dashboard, name='representative'),
    path('admin/', views.admin_dashboard, name='admin'),
]
