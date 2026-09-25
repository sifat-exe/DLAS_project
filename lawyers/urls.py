from django.urls import path
from . import views

app_name = 'lawyers'

urlpatterns = [
    path('', views.index, name='index'),
    path('assignments/<int:assignment_id>/respond/', views.respond_assignment, name='respond_assignment'),
    path('case/<str:case_id>/', views.lawyer_case_detail, name='case_detail'),
]
