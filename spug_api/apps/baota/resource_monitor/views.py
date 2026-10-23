from django.views import View

from libs import json_response
from apps.baota.validation import parse_body
from . import service


def respond(action, *args):
    try:
        return json_response(action(*args))
    except ValueError as exc:
        return json_response(error=str(exc))


class SourcesView(View):
    def get(self, request):
        return respond(service.sources, request.user, request.GET.get('kind'))


class CardsView(View):
    def get(self, request):
        return respond(service.cards, request.user)


class SettingsView(View):
    def post(self, request):
        return respond(lambda: service.settings(request.user, parse_body(request)))


class LinkView(View):
    def post(self, request):
        return respond(lambda: service.link(request.user, parse_body(request)))


class SampleView(View):
    def post(self, request):
        return respond(lambda: service.sample(request.user, parse_body(request)))
