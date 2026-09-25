import os
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404

from accounts.models import UserProfile
from accounts.permissions import has_role, can_access_case
from cases.models import CaseRecord
from documents.models import Document
from cases.services import upload_case_document, verify_case_document


def index(request):
    """
    Overview of accessible documents.
    Anonymous: renders informative index (HTTP 200).
    Staff: views all documents.
    Citizen / Lawyer: views authorized documents only.
    """
    documents = []
    if request.user.is_authenticated:
        user = request.user
        role = getattr(getattr(user, 'profile', None), 'role', None)

        if has_role(user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
            documents = Document.objects.all().select_related('case', 'uploaded_by').order_by('-created_at')
        elif role == UserProfile.ROLE_CITIZEN:
            documents = Document.objects.filter(case__application__applicant_user=user).select_related('case', 'uploaded_by').order_by('-created_at')
        elif role == UserProfile.ROLE_PANEL_LAWYER:
            documents = Document.objects.filter(case__assigned_lawyer=user).select_related('case', 'uploaded_by').order_by('-created_at')

    return render(request, 'documents/index.html', {'documents': documents})


@login_required
def document_upload(request, case_id):
    """
    Upload a document attached to a CaseRecord.
    Server-side authorization enforced via can_access_case.
    A citizen can never upload to or access another citizen's case documents.
    """
    case_record = get_object_or_404(CaseRecord, case_id=case_id)

    if not can_access_case(request.user, case_record):
        raise PermissionDenied("You are not authorized to upload documents for this case.")

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        file_obj = request.FILES.get('file')

        if not title:
            messages.error(request, "Document title is required.")
        elif not file_obj:
            messages.error(request, "Please choose a file to upload.")
        else:
            try:
                doc = upload_case_document(
                    case_record=case_record,
                    user=request.user,
                    title=title,
                    file_obj=file_obj,
                    description=description,
                )
                messages.success(request, f"Document '{doc.title}' uploaded successfully.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

    # Redirect to appropriate detail view based on role
    if has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        return redirect('cases:officer_case_detail', case_id=case_id)
    elif has_role(request.user, [UserProfile.ROLE_PANEL_LAWYER]):
        return redirect('lawyers:case_detail', case_id=case_id)
    else:
        return redirect('cases:application_detail', application_id=case_record.application.application_id)


@login_required
def document_download(request, document_id):
    """
    Secure document access endpoint.
    Prevents public or unauthenticated URL exposure.
    Verifies that requesting user has explicit case access permission.
    """
    document = get_object_or_404(Document, id=document_id)

    if not can_access_case(request.user, document.case):
        raise PermissionDenied("You do not have permission to access or download this document.")

    if not document.file or not os.path.exists(document.file.path):
        raise Http404("Document file not found on storage server.")

    filename = os.path.basename(document.file.name)
    return FileResponse(document.file.open('rb'), as_attachment=False, filename=filename)


@login_required
def document_verify(request, document_id):
    """
    Authorized staff changes document status (verified, missing, unreadable).
    Creates an immutable CaseEvent.
    AI systems are prohibited from automatic verification.
    """
    if not has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO personnel can verify documents.")

    document = get_object_or_404(Document, id=document_id)

    if request.method == 'POST':
        new_status = request.POST.get('status')
        review_notes = request.POST.get('notes', '').strip()

        try:
            verify_case_document(
                document=document,
                staff_user=request.user,
                new_status=new_status,
                review_notes=review_notes,
            )
            messages.success(request, f"Document '{document.title}' marked as {document.get_status_display()}.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))

    return redirect('cases:officer_case_detail', case_id=document.case.case_id)


@login_required
def document_sign(request, document_id):
    """
    Simulated electronic signature on a case document using MockSignatureService.
    Server-side authorization enforced via can_access_case.
    Clearly indicates simulated status and not legally binding.
    """
    from documents.models import Signature
    from core.mock_services import MockSignatureService

    document = get_object_or_404(Document, id=document_id)
    if not can_access_case(request.user, document.case):
        raise PermissionDenied("You do not have permission to sign documents for this case.")

    if request.method == 'POST':
        notes = request.POST.get('notes', '').strip()
        offline = (request.POST.get('offline_created') == 'true')
        try:
            sig = MockSignatureService.create_signature(
                document=document,
                signer_user=request.user,
                offline_created=offline,
                notes=notes
            )
            messages.success(
                request,
                f"[ SIMULATED ] Document '{document.title}' signed successfully. "
                f"SHA-256 Hash: {sig.document_hash[:16]}... (Not legally binding / আইনগতভাবে বাধ্যতামূলক নয়)"
            )
        except Exception as e:
            messages.error(request, str(e))

    # Redirect based on user role
    if has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        return redirect('cases:officer_case_detail', case_id=document.case.case_id)
    elif has_role(request.user, [UserProfile.ROLE_PANEL_LAWYER]):
        return redirect('lawyers:case_detail', case_id=document.case.case_id)
    elif has_role(request.user, [UserProfile.ROLE_MEDIATOR]) and hasattr(document.case, 'mediation'):
        return redirect('mediation:mediation_detail', mediation_id=document.case.mediation.id)
    else:
        return redirect('cases:application_detail', application_id=document.case.application.application_id)


@login_required
def document_verify_signature(request, signature_id):
    """
    Verifies a simulated electronic signature against the current document content hash.
    Enforces server-side authorization check.
    """
    from documents.models import Signature
    from core.mock_services import MockSignatureService

    signature = get_object_or_404(Signature, id=signature_id)
    if not can_access_case(request.user, signature.case):
        raise PermissionDenied("You do not have permission to verify signatures for this case.")

    if request.method == 'POST':
        result = MockSignatureService.verify_signature(signature)
        if result['is_valid']:
            messages.success(
                request,
                f"[ SIMULATED ] Signature verified successfully for document '{signature.document.title}'. "
                "Document cryptographic integrity confirmed. (Not legally binding / আইনগতভাবে বাধ্যতামূলক নয়)"
            )
        else:
            messages.error(
                request,
                f"[ SIMULATED ] Signature Verification Failed: Document hash mismatch. "
                "Document has been modified or corrupted since signing."
            )

    if has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        return redirect('cases:officer_case_detail', case_id=signature.case.case_id)
    elif has_role(request.user, [UserProfile.ROLE_PANEL_LAWYER]):
        return redirect('lawyers:case_detail', case_id=signature.case.case_id)
    else:
        return redirect('cases:application_detail', application_id=signature.case.application.application_id)

