from django.urls import path
from . import views

app_name = 'referrals'

urlpatterns = [
    path('', views.referral_list, name='index'),
    path('<int:referral_id>/', views.referral_detail, name='referral_detail'),
]
