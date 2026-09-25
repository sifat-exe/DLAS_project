/**
 * DLAS - Digital Legal Aid System
 * Vanilla JavaScript (No Frameworks)
 */

document.addEventListener('DOMContentLoaded', function () {
  // 1. Language switch preserving form data (PRD Req 52)
  const langButtons = document.querySelectorAll('.lang-btn');
  const forms = document.querySelectorAll('form');

  // Save form fields before navigating if switching language
  langButtons.forEach(btn => {
    btn.addEventListener('click', function (e) {
      if (forms.length > 0) {
        const formData = {};
        forms.forEach(form => {
          const elements = form.elements;
          for (let i = 0; i < elements.length; i++) {
            const el = elements[i];
            if (el.name && el.type !== 'password' && el.type !== 'hidden') {
              formData[el.name] = el.value;
            }
          }
        });
        sessionStorage.setItem('dlas_form_draft', JSON.stringify(formData));
      }
    });
  });

  // Restore form fields if saved during language switch
  const savedData = sessionStorage.getItem('dlas_form_draft');
  if (savedData) {
    try {
      const parsed = JSON.parse(savedData);
      Object.keys(parsed).forEach(key => {
        const input = document.querySelector(`[name="${key}"]`);
        if (input && !input.value) {
          input.value = parsed[key];
        }
      });
      // Clear after restore so it doesn't linger
      sessionStorage.removeItem('dlas_form_draft');
    } catch (err) {
      console.warn('Could not restore form data:', err);
    }
  }

  // 2. Prevent duplicate form submissions (PRD Req 260)
  forms.forEach(form => {
    form.addEventListener('submit', function (e) {
      const submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');
      if (submitBtn && !submitBtn.disabled) {
        // Allow form to submit once, then disable and show submitting text
        setTimeout(() => {
          submitBtn.disabled = true;
          const origText = submitBtn.innerText || submitBtn.value;
          submitBtn.setAttribute('data-original-text', origText);
          if (submitBtn.innerText) {
            submitBtn.innerText = 'Submitting... / জমা হচ্ছে...';
          }
        }, 50);
      }
    });
  });

  // 3. Auto-dismiss alerts after 5 seconds
  const alerts = document.querySelectorAll('.alert');
  alerts.forEach(alert => {
    setTimeout(() => {
      alert.style.transition = 'opacity 0.5s ease';
      alert.style.opacity = '0';
      setTimeout(() => alert.remove(), 500);
    }, 5000);
  });
});
