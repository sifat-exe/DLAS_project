from django.urls import path
from . import views

app_name = 'documents'

urlpatterns = [
    path('', views.index, name='index'),
    path('upload/<str:case_id>/', views.document_upload, name='document_upload'),
    path('<int:document_id>/download/', views.document_download, name='document_download'),
    path('<int:document_id>/verify/', views.document_verify, name='document_verify'),
    path('<int:document_id>/sign/', views.document_sign, name='document_sign'),
    path('signatures/<int:signature_id>/verify/', views.document_verify_signature, name='signature_verify'),
]

