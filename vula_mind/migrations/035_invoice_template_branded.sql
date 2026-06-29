-- 035_invoice_template_branded.sql — allow the 4th "branded" invoice template.
alter table commerce_invoice_settings drop constraint if exists commerce_invoice_settings_template_choice_check;
alter table commerce_invoice_settings
    add constraint commerce_invoice_settings_template_choice_check
    check (template_choice in ('classic','minimal','modern','branded'));
