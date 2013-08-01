from StringIO import StringIO
from datetime import datetime, timedelta
import re

from genshi.builder import tag

from trac.attachment import AttachmentModule
from trac.config import ConfigSection, ExtensionOption
from trac.core import *
from trac.perm import IPermissionRequestor
from trac.resource import ResourceNotFound, Resource, ResourceSystem, \
                          IResourceManager, get_resource_name, get_resource_url
from trac.search import ISearchSource, search_to_regexps, shorten_result
from trac.ticket.api import TicketSystem
from trac.ticket.roadmap import ITicketGroupStatsProvider, TicketGroupStats, \
                                DefaultTicketGroupStatsProvider, \
                                get_ticket_stats, grouped_stats_data, \
                                RoadmapModule
from trac.timeline.api import ITimelineEventProvider
#from trac.util.datefmt import parse_date, utc, to_utimestamp, to_datetime, \
#                              get_datetime_format_hint, format_date, \
#                              format_datetime, from_utimestamp, user_time
from trac.util.html import html
from trac.util.translation import _, tag_
from trac.web import IRequestHandler
from trac.web.chrome import (Chrome, INavigationContributor,
                             add_link, add_notice, add_script, add_stylesheet,
                             add_warning, auth_link, prevnext_nav, web_context)
from trac.wiki.api import IWikiSyntaxProvider
from trac.wiki.formatter import format_to

from multiproduct.ticket.model import BHComponent, ComponentCache, group_components

class ComponentModule(Component):
    implements(INavigationContributor, IRequestHandler, IResourceManager, 
               IPermissionRequestor, ITimelineEventProvider, ISearchSource,
               IWikiSyntaxProvider)

    stats_provider = ExtensionOption('roadmap', 'stats_provider',
                                     ITicketGroupStatsProvider,
                                     'DefaultTicketGroupStatsProvider',
        """Name of the component implementing `ITicketGroupStatsProvider`,
        which is used to collect statistics on groups of tickets for display
        in the roadmap views.""")

    # INavigationContributor methods
    
    def get_active_navigation_item(self, req):
        return 'component'
    
    def get_navigation_items(self, req):
        return []

    # IPermissionRequestor methods
    
    def get_permission_actions(self):
        actions = ['MILESTONE_CREATE', 'MILESTONE_DELETE', 'MILESTONE_MODIFY',
                   'MILESTONE_VIEW']
        return actions + [('MILESTONE_ADMIN', actions)]

    # IRequestHandler methods
    
    def match_request(self, req):
        match = re.match(r'/component/(.+)$', req.path_info)
        if match:
            req.args['id'] = match.group(1)
            return True
        else:
            return False
    
    def process_request(self, req):
        component_id = req.args.get('id')
        req.perm('component', component_id).require('MILESTONE_VIEW')

        add_link(req, 'up', req.href.roadmap(), _('Roadmap'))

        action = req.args.get('action', 'view')
        try:
            component = BHComponent(self.env, component_id)
        except ResourceNotFound:
            if 'MILESTONE_CREATE' not in req.perm('component', component_id):
                raise
            component = BHComponent(self.env, None)
            component.name = component_id
            action = 'edit' # rather than 'new' so that it works for POST/save

        if req.method == 'POST':
            if req.args.has_key('cancel'):
                if component.exists:
                    req.redirect(req.href.component(component.name))
                else:
                    req.redirect(req.href.roadmap())
            elif action == 'edit':
                return self._do_save(req, component)
            elif action == 'delete':
                self._do_delete(req, component)
        elif action in ('new', 'edit'):
            return self._render_editor(req, component)
        elif action == 'delete':
            return self._render_confirm(req, component)

        if not component.name:
            req.redirect(req.href.roadmap())

        return self._render_view(req, component)

    # IResourceManager methods
    
    def get_resource_realms(self):
        yield 'component'

    def get_resource_description(self, resource, format=None, context=None,
                                 **kwargs):
        nbhprefix = ResourceSystem(self.env).neighborhood_prefix(
                resource.neighborhood)
        desc = resource.id
        if format != 'compact':
            desc =  nbhprefix + _('Component %(name)s', name=resource.id)
        if context:
            return self._render_link(context, resource.id, desc)
        else:
            return desc

    def resource_exists(self, resource):
        """
        >>> from trac.test import EnvironmentStub
        >>> env = EnvironmentStub()

        >>> c1 = BHComponent(env)
        >>> c1.name = 'C1'
        >>> c1.insert()

        >>> ComponentModule(env).resource_exists(Resource('component', 'C1'))
        True
        >>> ComponentModule(env).resource_exists(Resource('component', 'C2'))
        False
        """
        return resource.id in ComponentCache(self.env).components

    # ISearchSource methods

    def get_search_filters(self, req):
        if 'MILESTONE_VIEW' in req.perm:
            yield ('component', _('Components'))

    def get_search_results(self, req, terms, filters):
        if not 'component' in filters:
            return
        term_regexps = search_to_regexps(terms)
        component_realm = Resource('component')
        for name, owner, description \
                in ComponentCache(self.env).components.itervalues():
            if any(r.search(description) or r.search(name)
                   for r in term_regexps):
                component = component_realm(id=name)
                if 'MILESTONE_VIEW' in req.perm(component):
                    yield (get_resource_url(self.env, component, req.href),
                           get_resource_name(self.env, component), owner,
                           shorten_result(description, terms))

        # Attachments
        for result in AttachmentModule(self.env).get_search_results(
                req, component_realm, terms):
            yield result

    # ITimelineEventProvider methods

    def get_timeline_filters(self, req):
        if 'MILESTONE_VIEW' in req.perm:
            yield ('component', _('Components reached'))

    def get_timeline_events(self, req, start, stop, filters):
        if 'component' in filters:
            component_realm = Resource('component')
            #for name, owner, description \
            #        in ComponentCache(self.env).components.itervalues():
            #    if completed and start <= completed <= stop:
            #        # TODO: creation and (later) modifications should also be
            #        #       reported
            #        component = component_realm(id=name)
            #        if 'MILESTONE_VIEW' in req.perm(component):
            #            yield ('component', completed, '', # FIXME: author?
            #                   (component, description))

            # Attachments
            for event in AttachmentModule(self.env).get_timeline_events(
                req, component_realm, start, stop):
                yield event

    def render_timeline_event(self, context, field, event):
        component, description = event[3]
        if field == 'url':
            return context.href.component(component.id)
        elif field == 'title':
            return tag_('Component %(name)s completed',
                        name=tag.em(component.id))
        elif field == 'description':
            return format_to(self.env, None, context.child(resource=component),
                             description)

    # IWikiSyntaxProvider methods

    def get_wiki_syntax(self):
        return []

    def get_link_resolvers(self):
        yield ('component', self._format_link)

    def _format_link(self, formatter, ns, name, label):
        name, query, fragment = formatter.split_link(name)
        return self._render_link(formatter.context, name, label,
                                 query + fragment)

    def _render_link(self, context, name, label, extra=''):
        try:
            component = BHComponent(self.env, name)
        except TracError:
            component = None
        # Note: the above should really not be needed, `BHComponent.exists`
        # should simply be false if the component doesn't exist in the db
        # (related to #4130)
        href = context.href.component(name)
        if component and component.exists:
            if 'MILESTONE_VIEW' in context.perm(component.resource):
                return tag.a(label, class_='component',
                             href=href + extra)
        elif 'MILESTONE_CREATE' in context.perm('component', name):
            return tag.a(label, class_='missing component', href=href + extra,
                         rel='nofollow')
        return tag.a(label, class_='missing component')
        
    # Internal methods

    def _do_delete(self, req, component):
        req.perm(component.resource).require('MILESTONE_DELETE')

        retarget_to = None
        if req.args.has_key('retarget'):
            retarget_to = req.args.get('target') or None
        component.delete(retarget_to, req.authname)
        add_notice(req, _('The component "%(name)s" has been deleted.',
                          name=component.name))
        req.redirect(req.href.roadmap())

    def _do_save(self, req, component):
        if component.exists:
            req.perm(component.resource).require('MILESTONE_MODIFY')
        else:
            req.perm(component.resource).require('MILESTONE_CREATE')

        old_name = component.name
        new_name = req.args.get('name')

        component.description = req.args.get('description', '')

        #if 'due' in req.args:
        #    due = req.args.get('duedate', '')
        #    component.due = user_time(req, parse_date, due, hint='datetime') \
        #                    if due else None
        #else:
        #    component.due = None

        #completed = req.args.get('completeddate', '')
        retarget_to = req.args.get('target')

        # Instead of raising one single error, check all the constraints and
        # let the user fix them by going back to edit mode showing the warnings
        warnings = []
        def warn(msg):
            add_warning(req, msg)
            warnings.append(msg)

        # -- check the name
        # If the name has changed, check that the component doesn't already
        # exist
        # FIXME: the whole .exists business needs to be clarified
        #        (#4130) and should behave like a WikiPage does in
        #        this respect.
        try:
            new_component = BHComponent(self.env, new_name)
            if new_component.name == old_name:
                pass        # Creation or no name change
            elif new_component.name:
                warn(_('Component "%(name)s" already exists, please '
                       'choose another name.', name=new_component.name))
            else:
                warn(_('You must provide a name for the component.'))
        except ResourceNotFound:
            component.name = new_name

        # -- check completed date
        #if 'completed' in req.args:
        #    completed = user_time(req, parse_date, completed,
        #                          hint='datetime') if completed else None
        #    if completed and completed > datetime.now(utc):
        #        warn(_('Completion date may not be in the future'))
        #else:
        #    completed = None
        #component.completed = completed

        if warnings:
            return self._render_editor(req, component)

        # -- actually save changes
        if component.exists:
            component.update()
            # eventually retarget opened tickets associated with the component
            #if 'retarget' in req.args and completed:
            #    self.env.db_transaction("""
            #        UPDATE ticket SET component=%s
            #        WHERE component=%s
            #        """, (retarget_to, old_name))
            #    self.log.info("Tickets associated with component %s "
            #                  "retargeted to %s" % (old_name, retarget_to))
        else:
            component.insert()

        add_notice(req, _("Your changes have been saved."))
        req.redirect(req.href.component(component.name))

    def _render_confirm(self, req, component):
        req.perm(component.resource).require('MILESTONE_DELETE')

        components = [m for m in BHComponent.select(self.env)
                      if m.name != component.name
                      and 'MILESTONE_VIEW' in req.perm(m.resource)]
        data = {
            'component': component,
            'component_groups': group_components(components)
        }
        return 'component_delete.html', data, None

    def _render_editor(self, req, component):
        # Suggest a default due time of 18:00 in the user's timezone
        #now = datetime.now(req.tz)
        #default_due = datetime(now.year, now.month, now.day, 18)
        #if now.hour > 18:
        #    default_due += timedelta(days=1)
        #default_due = to_datetime(default_due, req.tz)

        data = {
            'component': component,
        #    'datetime_hint': get_datetime_format_hint(req.lc_time),
        #    'default_due': default_due,
            'component_groups': [],
        }

        if component.exists:
            req.perm(component.resource).require('MILESTONE_MODIFY')
            components = [m for m in BHComponent.select(self.env)
                          if m.name != component.name
                          and 'MILESTONE_VIEW' in req.perm(m.resource)]
            data['component_groups'] = group_components(components)
        else:
            req.perm(component.resource).require('MILESTONE_CREATE')

        chrome = Chrome(self.env)
        chrome.add_jquery_ui(req)
        chrome.add_wiki_toolbars(req)
        return 'component_edit.html', data, None

    def _render_view(self, req, component):
        component_groups = []
        available_groups = []
        component_group_available = False
        ticket_fields = TicketSystem(self.env).get_ticket_fields()

        # collect fields that can be used for grouping
        for field in ticket_fields:
            if field['type'] == 'select' and field['name'] != 'component' \
                    or field['name'] in ('owner', 'reporter'):
                available_groups.append({'name': field['name'],
                                         'label': field['label']})
                if field['name'] == 'component':
                    component_group_available = True

        # determine the field currently used for grouping
        by = None
        #if component_group_available:
        #    by = 'component'
        #el
        if available_groups:
            by = available_groups[0]['name']
        by = req.args.get('by', by)

        tickets = get_tickets_for_component(self.env, component=component.name,
                                            field=by)
        tickets = apply_ticket_permissions(self.env, req, tickets)
        stat = get_ticket_stats(self.stats_provider, tickets)

        context = web_context(req, component.resource)
        data = {
            'context': context,
            'component': component,
            'attachments': AttachmentModule(self.env).attachment_data(context),
            'available_groups': available_groups,
            'grouped_by': by,
            'groups': component_groups
            }
        data.update(component_stats_data(self.env, req, stat, component.name))

        if by:
            def per_group_stats_data(gstat, group_name):
                return component_stats_data(self.env, req, gstat,
                                            component.name, by, group_name)
            component_groups.extend(
                grouped_stats_data(self.env, self.stats_provider, tickets, by,
                                   per_group_stats_data))

        add_stylesheet(req, 'common/css/roadmap.css')
        add_script(req, 'common/js/folding.js')

        def add_component_link(rel, component):
            href = req.href.component(component.name, by=req.args.get('by'))
            add_link(req, rel, href, _('Component "%(name)s"',
                                       name=component.name))

        components = [m for m in BHComponent.select(self.env)
                      if 'MILESTONE_VIEW' in req.perm(m.resource)]
        idx = [i for i, m in enumerate(components) if m.name == component.name]
        if idx:
            idx = idx[0]
            if idx > 0:
                add_component_link('first', components[0])
                add_component_link('prev', components[idx - 1])
            if idx < len(components) - 1:
                add_component_link('next', components[idx + 1])
                add_component_link('last', components[-1])
        roadmap_back = self.env[RoadmapModule] and _('Back to Roadmap') or None
        prevnext_nav(req, _('Previous Component'), _('Next Component'),
                         roadmap_back)

        return 'bh_component_view.html', data, None

def get_tickets_for_component(env, db=None, component=None, field='component'):
    """Retrieve all tickets associated with the given `component`.

    .. versionchanged :: 1.0
       the `db` parameter is no longer needed and will be removed in
       version 1.1.1
    """
    with env.db_query as db:
        fields = TicketSystem(env).get_ticket_fields()
        if field in [f['name'] for f in fields if not f.get('custom')]:
            sql = """SELECT id, status, %s FROM ticket WHERE component=%%s
                     ORDER BY %s""" % (field, field)
            args = (component,)
        else:
            sql = """SELECT id, status, value FROM ticket
                       LEFT OUTER JOIN ticket_custom ON (id=ticket AND name=%s)
                      WHERE component=%s ORDER BY value"""
            args = (field, component)
        return [{'id': tkt_id, 'status': status, field: fieldval}
                for tkt_id, status, fieldval in env.db_query(sql, args)]

def apply_ticket_permissions(env, req, tickets):
    """Apply permissions to a set of component tickets as returned by
    `get_tickets_for_component()`."""
    return [t for t in tickets
            if 'TICKET_VIEW' in req.perm('ticket', t['id'])]

def component_stats_data(env, req, stat, name, grouped_by='component',
                         group=None):
    from trac.ticket.query import QueryModule
    has_query = env[QueryModule] is not None
    def query_href(extra_args):
        if not has_query:
            return None
        args = {'component': name, grouped_by: group, 'group': 'status'}
        args.update(extra_args)
        return req.href.query(args)
    return {'stats': stat,
            'stats_href': query_href(stat.qry_args),
            'interval_hrefs': [query_href(interval['qry_args'])
                               for interval in stat.intervals]}

