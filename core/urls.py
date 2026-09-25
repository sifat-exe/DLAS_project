from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.landing, name='landing'),
    path('set-language/<str:lang>/', views.set_language, name='set_language'),
]
