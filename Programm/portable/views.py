import os
from django.http import JsonResponse

def status(request):
    return JsonResponse({'instance':os.environ.get('PORTABLE_INSTANCE_ID','')})
