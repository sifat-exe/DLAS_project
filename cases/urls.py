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

    # Batch 2: Conversational Intake
    path('intake/chat/', views.conversational_intake_view, name='conversational_intake'),
    path('intake/chat/message/', views.conversational_intake_message, name='conversational_intake_message'),
    path('intake/chat/confirm/', views.conversational_intake_confirm, name='conversational_intake_confirm'),
    path('intake/chat/reset/', views.conversational_intake_reset, name='conversational_intake_reset'),

    # Batch 2: Moyuri's Confirmation
    path('intake/moyuri-confirm/', views.moyuri_confirmation_view, name='moyuri_confirm'),

    # Batch 2: Ripon Voice-Only Task
    path('tasks/<int:task_id>/voice/', views.voice_task_view, name='voice_task'),
    path('tasks/<int:task_id>/voice/execute/', views.voice_task_execute, name='voice_task_execute'),
    path('tasks/ripon-demo/', views.ripon_voice_task_demo, name='ripon_voice_task_demo'),
]
