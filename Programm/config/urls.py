from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from tracker import views
urlpatterns=[
 path('admin/',admin.site.urls),
 path('anmeldung/',auth.LoginView.as_view(template_name='registration/login.html'),name='login'),
 path('abmeldung/',auth.LogoutView.as_view(),name='logout'),
 path('',views.home,name='home'),path('stempeln/',views.punch,name='punch'),
 path('woche/',views.week,name='week'),path('profil/',views.profile,name='profile'),
 path('urlaub/<int:pk>/zurueckziehen/',views.withdraw,name='withdraw'),
 path('plan/<int:pk>/',views.plan_edit,name='plan'),path('plan/<int:pk>/dokument/',views.download,name='document'),path('plan/<int:pk>/neue-fassung/',views.copy_plan,name='copy_plan'),
 path('verwaltung/student/<int:pk>/plan/',views.new_plan,name='new_plan'),
 path('verwaltung/student/<int:pk>/nachtragen/',views.new_interval,name='new_interval'),
 path('verwaltung/',views.dashboard,name='dashboard'),path('verwaltung/plan/<int:pk>/',views.review,name='review'),path('verwaltung/plan/<int:pk>/zurueck/',views.return_plan,name='return_plan'),
 path('verwaltung/urlaub/<int:pk>/',views.leave_decision,name='leave_decision'),path('verwaltung/buchung/<int:pk>/',views.correct,name='correct'),
 path('verwaltung/bericht/<int:pk>/',views.report_detail,name='report'),path('verwaltung/bericht/<int:pk>/csv/',views.report_csv,name='report_csv'),path('verwaltung/bericht/<int:pk>/versand/',views.report_action,name='report_action')]

from django.conf import settings
if getattr(settings,'PORTABLE',False):
    from portable.views import status
    urlpatterns.append(path('lokaler-status/',status))
