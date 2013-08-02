#  Licensed to the Apache Software Foundation (ASF) under one
#  or more contributor license agreements.  See the NOTICE file
#  distributed with this work for additional information
#  regarding copyright ownership.  The ASF licenses this file
#  to you under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance
#  with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing,
#  software distributed under the License is distributed on an
#  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#  KIND, either express or implied.  See the License for the
#  specific language governing permissions and limitations
#  under the License.

from StringIO import StringIO
from datetime import datetime, timedelta
import re

from genshi.builder import tag

from trac.attachment import AttachmentModule
from trac.config import ConfigSection, ExtensionOption
from trac.core import *
from trac.perm import IPermissionRequestor
from trac.resource import (ResourceNotFound, Resource, ResourceSystem,
                           IResourceManager, get_resource_name, 
                           get_resource_url)
from trac.search import ISearchSource, search_to_regexps, shorten_result
from trac.ticket.api import TicketSystem
from trac.ticket.roadmap import (ITicketGroupStatsProvider, TicketGroupStats,
                                 DefaultTicketGroupStatsProvider,
                                 get_ticket_stats, grouped_stats_data,
                                 RoadmapModule)
from trac.timeline.api import ITimelineEventProvider
from trac.util.datefmt import (parse_date, utc, to_utimestamp, to_datetime,
                               get_datetime_format_hint, format_date,
                               format_datetime, from_utimestamp, user_time)
from trac.util.html import html
from trac.util.translation import _, tag_
from trac.web import IRequestHandler
from trac.web.chrome import (Chrome, INavigationContributor,
                             add_link, add_notice, add_script, add_stylesheet,
                             add_warning, auth_link, prevnext_nav, web_context)
from trac.wiki.api import IWikiSyntaxProvider
from trac.wiki.formatter import format_to

from multiproduct.ticket.model import BHVersion, VersionCache, group_versions

class VersionModule(Component):
    implements(INavigationContributor, IRequestHandler, IResourceManager, 
               IPermissionRequestor, ITimelineEventProvider, ISearchSource,
               IWikiSyntaxProvider)

    stats_provider = ExtensionOption('roadmap', 'stats_provider',
                                     ITicketGroupStatsProvider,
                                     'DefaultTicketGroupStatsProvider',
        """Name of the version implementing `ITicketGroupStatsProvider`,
        which is used to collect statistics on groups of tickets for display
        in the roadmap views.""")

    # INavigationContributor methods
    
    def get_active_navigation_item(self, req):
        return 'version'
    
    def get_navigation_items(self, req):
        return []

    # IPermissionRequestor methods
    
    def get_permission_actions(self):
        actions = ['MILESTONE_CREATE', 'MILESTONE_DELETE', 'MILESTONE_MODIFY',
                   'MILESTONE_VIEW']
        return actions + [('MILESTONE_ADMIN', actions)]

    # IRequestHandler methods
    
    def match_request(self, req):
        match = re.match(r'/version/(.+)$', req.path_info)
        if match:
            req.args['id'] = match.group(1)
            return True
        else:
            return False
    
    def process_request(self, req):
        version_id = req.args.get('id')
        req.perm('version', version_id).require('MILESTONE_VIEW')

        add_link(req, 'up', req.href.roadmap(), _('Roadmap'))

        action = req.args.get('action', 'view')
        try:
            version = BHVersion(self.env, version_id)
        except ResourceNotFound:
            if 'MILESTONE_CREATE' not in req.perm('version', version_id):
                raise
            version = BHVersion(self.env, None)
            version.name = version_id
            action = 'edit' # rather than 'new' so that it works for POST/save

        if req.method == 'POST':
            if req.args.has_key('cancel'):
                if version.exists:
                    req.redirect(req.href.version(version.name))
                else:
                    req.redirect(req.href.roadmap())
            elif action == 'edit':
                return self._do_save(req, version)
            elif action == 'delete':
                self._do_delete(req, version)
        elif action in ('new', 'edit'):
            return self._render_editor(req, version)
        elif action == 'delete':
            return self._render_confirm(req, version)

        if not version.name:
            req.redirect(req.href.roadmap())

        return self._render_view(req, version)

    # IResourceManager methods
    
    def get_resource_realms(self):
        yield 'version'

    def get_resource_description(self, resource, format=None, context=None,
                                 **kwargs):
        nbhprefix = ResourceSystem(self.env).neighborhood_prefix(
                resource.neighborhood)
        desc = resource.id
        if format != 'compact':
            desc =  nbhprefix + _('Version %(name)s', name=resource.id)
        if context:
            return self._render_link(context, resource.id, desc)
        else:
            return desc

    def resource_exists(self, resource):
        """
        >>> from trac.test import EnvironmentStub
        >>> env = EnvironmentStub()

        >>> v1 = BHVersion(env)
        >>> v1.name = 'V1'
        >>> v1.insert()

        >>> VersionModule(env).resource_exists(Resource('version', 'V1'))
        True
        >>> VersionModule(env).resource_exists(Resource('version', 'V2'))
        False
        """
        return resource.id in VersionCache(self.env).versions

    # ISearchSource methods

    def get_search_filters(self, req):
        if 'MILESTONE_VIEW' in req.perm:
            yield ('version', _('Versions'))

    def get_search_results(self, req, terms, filters):
        if not 'version' in filters:
            return
        term_regexps = search_to_regexps(terms)
        version_realm = Resource('version')
        for name, time, description \
                in VersionCache(self.env).versions.itervalues():
            if any(r.search(description) or r.search(name)
                   for r in term_regexps):
                version = version_realm(id=name)
                if 'MILESTONE_VIEW' in req.perm(version):
                    dt = (time if time else datetime.now(utc))
                    yield (get_resource_url(self.env, version, req.href),
                           get_resource_name(self.env, version), dt,
                           '', shorten_result(description, terms))

        # Attachments
        for result in AttachmentModule(self.env).get_search_results(
                req, version_realm, terms):
            yield result

    # ITimelineEventProvider methods

    def get_timeline_filters(self, req):
        if 'MILESTONE_VIEW' in req.perm:
            yield ('version', _('Versions reached'))

    def get_timeline_events(self, req, start, stop, filters):
        if 'version' in filters:
            version_realm = Resource('version')
            for name, time, description \
                    in VersionCache(self.env).versions.itervalues():
                if time and start <= time <= stop:
                    # TODO: creation and (later) modifications should also be
                    #       reported
                    version = version_realm(id=name)
                    if 'MILESTONE_VIEW' in req.perm(version):
                        yield ('version', time, '', # FIXME: author?
                               (version, description))

            # Attachments
            for event in AttachmentModule(self.env).get_timeline_events(
                req, version_realm, start, stop):
                yield event

    def render_timeline_event(self, context, field, event):
        version, description = event[3]
        if field == 'url':
            return context.href.version(version.id)
        elif field == 'title':
            return tag_('Version %(name)s completed',
                        name=tag.em(version.id))
        elif field == 'description':
            return format_to(self.env, None, context.child(resource=version),
                             description)

    # IWikiSyntaxProvider methods

    def get_wiki_syntax(self):
        return []

    def get_link_resolvers(self):
        yield ('version', self._format_link)

    def _format_link(self, formatter, ns, name, label):
        name, query, fragment = formatter.split_link(name)
        return self._render_link(formatter.context, name, label,
                                 query + fragment)

    def _render_link(self, context, name, label, extra=''):
        try:
            version = BHVersion(self.env, name)
        except TracError:
            version = None
        # Note: the above should really not be needed, `BHVersion.exists`
        # should simply be false if the version doesn't exist in the db
        # (related to #4130)
        href = context.href.version(name)
        if version and version.exists:
            if 'MILESTONE_VIEW' in context.perm(version.resource):
                return tag.a(label, class_='version',
                             href=href + extra)
        elif 'MILESTONE_CREATE' in context.perm('version', name):
            return tag.a(label, class_='missing version', href=href + extra,
                         rel='nofollow')
        return tag.a(label, class_='missing version')
        
    # Internal methods

    def _do_delete(self, req, version):
        req.perm(version.resource).require('MILESTONE_DELETE')

        retarget_to = None
        if req.args.has_key('retarget'):
            retarget_to = req.args.get('target') or None
        version.delete(retarget_to, req.authname)
        add_notice(req, _('The version "%(name)s" has been deleted.',
                          name=version.name))
        req.redirect(req.href.roadmap())

    def _do_save(self, req, version):
        if version.exists:
            req.perm(version.resource).require('MILESTONE_MODIFY')
        else:
            req.perm(version.resource).require('MILESTONE_CREATE')

        old_name = version.name
        new_name = req.args.get('name')

        version.description = req.args.get('description', '')

        if 'due' in req.args:
            time = req.args.get('duedate', '')
            version.time = user_time(req, parse_date, time, hint='datetime') \
                            if time else None
        else:
            version.time = None

        retarget_to = req.args.get('target')

        # Instead of raising one single error, check all the constraints and
        # let the user fix them by going back to edit mode showing the warnings
        warnings = []
        def warn(msg):
            add_warning(req, msg)
            warnings.append(msg)

        # -- check the name
        # If the name has changed, check that the version doesn't already
        # exist
        # FIXME: the whole .exists business needs to be clarified
        #        (#4130) and should behave like a WikiPage does in
        #        this respect.
        try:
            new_version = BHVersion(self.env, new_name)
            if new_version.name == old_name:
                pass        # Creation or no name change
            elif new_version.name:
                warn(_('Version "%(name)s" already exists, please '
                       'choose another name.', name=new_version.name))
            else:
                warn(_('You must provide a name for the version.'))
        except ResourceNotFound:
            version.name = new_name

        # -- check completed date
        #if 'completed' in req.args:
        #    completed = user_time(req, parse_date, completed,
        #                          hint='datetime') if completed else None
        #    if completed and completed > datetime.now(utc):
        #        warn(_('Completion date may not be in the future'))
        #else:
        #    completed = None
        #version.completed = completed

        if warnings:
            return self._render_editor(req, version)
        
        # -- actually save changes
        if version.exists:
            version.update()
            # eventually retarget opened tickets associated with the version
            if 'retarget' in req.args and completed:
                self.env.db_transaction("""
                    UPDATE ticket SET version=%s
                    WHERE version=%s
                    """, (retarget_to, old_name))
                self.log.info("Tickets associated with version %s "
                              "retargeted to %s" % (old_name, retarget_to))
        else:
            version.insert()

        add_notice(req, _("Your changes have been saved."))
        req.redirect(req.href.version(version.name))

    def _render_confirm(self, req, version):
        req.perm(version.resource).require('MILESTONE_DELETE')

        versions = [m for m in BHVersion.select(self.env)
                      if m.name != version.name
                      and 'MILESTONE_VIEW' in req.perm(m.resource)]
        data = {
            'version': version,
            'version_groups': group_versions(versions)
        }
        return 'version_delete.html', data, None

    def _render_editor(self, req, version):
        # Suggest a default due time of 18:00 in the user's timezone
        now = datetime.now(req.tz)
        default_due = datetime(now.year, now.month, now.day, 18)
        if now.hour > 18:
            default_due += timedelta(days=1)
        default_due = to_datetime(default_due, req.tz)

        data = {
            'version': version,
            'datetime_hint': get_datetime_format_hint(req.lc_time),
            'default_due': default_due,
            'version_groups': [],
        }

        if version.exists:
            req.perm(version.resource).require('MILESTONE_MODIFY')
            versions = [m for m in BHVersion.select(self.env)
                          if m.name != version.name
                          and 'MILESTONE_VIEW' in req.perm(m.resource)]
            data['version_groups'] = group_versions(versions)
        else:
            req.perm(version.resource).require('MILESTONE_CREATE')

        chrome = Chrome(self.env)
        chrome.add_jquery_ui(req)
        chrome.add_wiki_toolbars(req)
        return 'version_edit.html', data, None

    def _render_view(self, req, version):
        version_groups = []
        available_groups = []
        #version_group_available = False
        ticket_fields = TicketSystem(self.env).get_ticket_fields()

        # collect fields that can be used for grouping
        for field in ticket_fields:
            if field['type'] == 'select' and field['name'] != 'version' \
                    or field['name'] in ('owner', 'reporter'):
                available_groups.append({'name': field['name'],
                                         'label': field['label']})
                #if field['name'] == 'version':
                #    version_group_available = True

        # determine the field currently used for grouping
        by = None
        #if version_group_available:
        #    by = 'version'
        #el
        if available_groups:
            by = available_groups[0]['name']
        by = req.args.get('by', by)

        tickets = get_tickets_for_version(self.env, version=version.name,
                                            field=by)
        tickets = apply_ticket_permissions(self.env, req, tickets)
        stat = get_ticket_stats(self.stats_provider, tickets)

        context = web_context(req, version.resource)
        data = {
            'context': context,
            'version': version,
            'attachments': AttachmentModule(self.env).attachment_data(context),
            'available_groups': available_groups,
            'grouped_by': by,
            'groups': version_groups
            }
        data.update(version_stats_data(self.env, req, stat, version.name))

        if by:
            def per_group_stats_data(gstat, group_name):
                return version_stats_data(self.env, req, gstat,
                                            version.name, by, group_name)
            version_groups.extend(
                grouped_stats_data(self.env, self.stats_provider, tickets, by,
                                   per_group_stats_data))

        add_stylesheet(req, 'common/css/roadmap.css')
        add_script(req, 'common/js/folding.js')

        def add_version_link(rel, version):
            href = req.href.version(version.name, by=req.args.get('by'))
            add_link(req, rel, href, _('Version "%(name)s"',
                                       name=version.name))

        versions = [m for m in BHVersion.select(self.env)
                      if 'MILESTONE_VIEW' in req.perm(m.resource)]
        idx = [i for i, m in enumerate(versions) if m.name == version.name]
        if idx:
            idx = idx[0]
            if idx > 0:
                add_version_link('first', versions[0])
                add_version_link('prev', versions[idx - 1])
            if idx < len(versions) - 1:
                add_version_link('next', versions[idx + 1])
                add_version_link('last', versions[-1])
        roadmap_back = self.env[RoadmapModule] and _('Back to Roadmap') or None
        prevnext_nav(req, _('Previous Version'), _('Next Version'),
                         roadmap_back)

        return 'bh_version_view.html', data, None

def get_tickets_for_version(env, db=None, version=None, field='version'):
    """Retrieve all tickets associated with the given `version`.

    .. versionchanged :: 1.0
       the `db` parameter is no longer needed and will be removed in
       version 1.1.1
    """
    with env.db_query as db:
        fields = TicketSystem(env).get_ticket_fields()
        if field in [f['name'] for f in fields if not f.get('custom')]:
            sql = """SELECT id, status, %s FROM ticket WHERE version=%%s
                     ORDER BY %s""" % (field, field)
            args = (version,)
        else:
            sql = """SELECT id, status, value FROM ticket
                       LEFT OUTER JOIN ticket_custom ON (id=ticket AND name=%s)
                      WHERE version=%s ORDER BY value"""
            args = (field, version)
        return [{'id': tkt_id, 'status': status, field: fieldval}
                for tkt_id, status, fieldval in env.db_query(sql, args)]

def apply_ticket_permissions(env, req, tickets):
    """Apply permissions to a set of version tickets as returned by
    `get_tickets_for_version()`."""
    return [t for t in tickets
            if 'TICKET_VIEW' in req.perm('ticket', t['id'])]

def version_stats_data(env, req, stat, name, grouped_by='version',
                         group=None):
    from trac.ticket.query import QueryModule
    has_query = env[QueryModule] is not None
    def query_href(extra_args):
        if not has_query:
            return None
        args = {'version': name, grouped_by: group, 'group': 'status'}
        args.update(extra_args)
        return req.href.query(args)
    return {'stats': stat,
            'stats_href': query_href(stat.qry_args),
            'interval_hrefs': [query_href(interval['qry_args'])
                               for interval in stat.intervals]}
