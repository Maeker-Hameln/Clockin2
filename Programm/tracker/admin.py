from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils.html import format_html
from .models import Plan,Segment,Interval,Pause,Leave,Audit,Report,Policy
class RoleAccess:
    def has_module_permission(self,request): return request.user.is_active and request.user.is_staff
    def has_view_permission(self,request,obj=None): return self.has_module_permission(request)
    def has_change_permission(self,request,obj=None): return self.has_module_permission(request)
    def has_add_permission(self,request): return self.has_module_permission(request)
    def has_delete_permission(self,request,obj=None): return False
admin.site.unregister(get_user_model())
admin.site.unregister(Group)
@admin.register(get_user_model())
class Accounts(RoleAccess,UserAdmin):
    fieldsets=((None,{'fields':('username','password')}),('Name',{'fields':('first_name','last_name')}),('Rolle und Zugang',{'fields':('is_active','is_staff')}))
    list_display=('username','first_name','last_name','is_active','is_staff')
    list_filter=('is_active','is_staff')
    filter_horizontal=()
    def formfield_for_dbfield(self,db_field,request,**kwargs):
        if db_field.name=='is_staff':
            kwargs.update(label='Administrator',help_text='Verwaltet alle Studenten, Pläne und Buchungen. Ohne diese Rolle ist das Konto ein Studentenkonto.')
        return super().formfield_for_dbfield(db_field,request,**kwargs)
    def get_readonly_fields(self,request,obj=None):
        return ('is_staff','is_active') if obj and obj.pk==request.user.pk else ()
@admin.register(Policy)
class Settings(RoleAccess,admin.ModelAdmin):
    def get_readonly_fields(self,request,obj=None):
        # Preserve historical calculations; dated changes belong in new plan sections.
        return ('state','rules','confirmed') if obj and obj.confirmed else ()
    def has_add_permission(self,request): return not Policy.objects.exists() and super().has_add_permission(request)
    def save_model(self,request,obj,form,change):
        before=Policy.objects.filter(pk=1).values().first() or {}
        for data in [before]:
            if data.get('report_from'):data['report_from']=str(data['report_from'])
        super().save_model(request,obj,form,change)
        after={f.name:str(getattr(obj,f.name)) for f in obj._meta.fields}
        Audit.objects.create(actor=request.user,kind='policy',object_id=1,before=before,after=after,reason='Betriebliche Konfiguration ausdrücklich gespeichert')
class ReadOnly(RoleAccess,admin.ModelAdmin):
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
@admin.register(Audit)
class AuditAdmin(ReadOnly):
    list_display=('at','actor','kind','object_id','reason')
    list_filter=('kind',);search_fields=('reason',)
@admin.register(Interval)
class IntervalAdmin(ReadOnly):
    list_display=('student','start','end','correction_link')
    search_fields=('student__username',)
    list_filter=('student',)
    def correction_link(self,obj): return format_html('<a href="{}">Mit Begründung korrigieren</a>',reverse('correct',args=[obj.pk]))
    correction_link.short_description='Korrektur'
@admin.register(Plan)
class PlanAdmin(ReadOnly):
    list_display=('student','created_at','status','application_link')
    list_filter=('student','status')
    def application_link(self,obj): return format_html('<a href="{}">Fassung öffnen</a>',reverse('plan',args=[obj.pk]))
    application_link.short_description='Planoberfläche'
@admin.register(Report)
class ReportAdmin(ReadOnly):
    list_display=('month','recipient','revision','status','application_link')
    list_filter=('status','month')
    def application_link(self,obj): return format_html('<a href="{}">Berichtsstand öffnen</a>',reverse('report',args=[obj.pk]))
    application_link.short_description='Bericht'
for model in [Segment,Pause,Leave]:admin.site.register(model,ReadOnly)
admin.site.site_header='Maeker · Verwaltung'
admin.site.site_title='Maeker Zeiterfassung'
admin.site.index_title='Benutzer und betriebliche Einstellungen'
