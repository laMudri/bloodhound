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

# Extra classes for trac.ticket.model

from __future__ import with_statement

from trac.ticket.model import Ticket, simplify_whitespace

import re
from datetime import datetime

from trac.attachment import Attachment
from trac import core
from trac.cache import cached
from trac.core import TracError
from trac.resource import Resource, ResourceNotFound, ResourceSystem
from trac.ticket.api import TicketSystem
from trac.util import embedded_numbers, partition
from trac.util.text import empty
from trac.util.datefmt import from_utimestamp, to_utimestamp, utc, utcmax
from trac.util.translation import _

class ComponentCache(core.Component):
    """Cache for component data and factory for 'component' resources."""

    @cached
    def components(self):
        """Dictionary containing component data, indexed by name.

        Component data consist of a tuple containing the name, the
        owner and the description.
        """
        components = {}
        for name, owner, description in self.env.db_query("""
                SELECT name, owner, description FROM component
                """):
            components[name] = (name, owner or '', description or '')
        return components

    def fetchone(self, name, component=None):
        """Retrieve an existing component having the given `name`.

        If `component` is specified, fill that instance instead of creating
        a fresh one.
     retu
        :return: `None` if no such component exists
        """
        data = self.components.get(name)
        if data:
            return self.factory(data, component)

    def fetchall(self):
        """Iterator on all components."""
        for data in self.components.itervalues():
            yield self.factory(data)

    def factory(self, (name, owner, description), component=None):
        """Build a `Component` object from component data.

        That instance remains *private*, i.e. can't be retrieved by
        name by other processes or even by other threads in the same
        process, until its `~Component.insert` method gets called with
        success.
        """
        component = component or BHComponent(self.env)
        component.name = name
        component.owner = owner
        component.description = description
        component.checkin(invalidate=False)
        return component
        
def group_components(components):
    """Wrap components in a group, like what happens to components."""
    return [(_('All'), components)]

class BHComponent(object):
    def __init__(self, env, name=None, db=None):
        """Create an undefined component or fetch one from the database,
        if `name` is given.

        In the latter case however, raise `~trac.resource.ResourceNotFound`
        if a component of that name doesn't exist yet.
        """
        self.env = env
        if name:
            if not self.cache.fetchone(name, self):
                raise ResourceNotFound(
                    _("Component %(name)s does not exist.",
                      name=name), _("Invalid component name"))
        else:
            self.cache.factory((None, None, ''), self)

    @property
    def cache(self):
        return ComponentCache(self.env)

    @property
    def resource(self):
        return Resource('component', self.name) ### .version !!!

    exists = property(lambda self: self._old['name'] is not None)

    def checkin(self, invalidate=True):
        self._old = {'name': self.name, 'description': self.description}
        if invalidate:
            del self.cache.components

    _to_old = checkin #: compatibility with hacks < 0.12.5 (remove in 1.1.1)

    def delete(self, retarget_to=None, author=None, db=None):
        """Delete the component.

        :since 1.0: the `db` parameter is no longer needed and will be removed
        in version 1.1.1
        """
        with self.env.db_transaction as db:
            self.env.log.info("Deleting component %s", self.name)
            db("DELETE FROM component WHERE name=%s", (self.name,))

            # Retarget/reset tickets associated with this component
            now = datetime.now(utc)
            tkt_ids = [int(row[0]) for row in
                       db("SELECT id FROM ticket WHERE component=%s",
                          (self.name,))]
            for tkt_id in tkt_ids:
                ticket = Ticket(self.env, tkt_id, db)
                ticket['component'] = retarget_to
                comment = "Component %s deleted" % self.name # don't translate
                ticket.save_changes(author, comment, now)
            self._old['name'] = None
            del self.cache.components
            TicketSystem(self.env).reset_ticket_fields()

        #for listener in TicketSystem(self.env).component_change_listeners:
        #    listener.component_deleted(self)
        ResourceSystem(self.env).resource_deleted(self)

    def insert(self, db=None):
        """Insert a new component.

        :since 1.0: the `db` parameter is no longer needed and will be removed
        in version 1.1.1
        """
        self.name = simplify_whitespace(self.name)
        if not self.name:
            raise TracError(_("Invalid component name."))

        with self.env.db_transaction as db:
            self.env.log.debug("Creating new component '%s'", self.name)
            db("""INSERT INTO component (name, description)
                  VALUES (%s,%s)
                  """, (self.name, self.description))
            self.checkin()
            TicketSystem(self.env).reset_ticket_fields()

        #for listener in TicketSystem(self.env).component_change_listeners:
        #    listener.component_created(self)
        ResourceSystem(self.env).resource_created(self)

    def update(self, db=None):
        """Update the component.

        :since 1.0: the `db` parameter is no longer needed and will be removed
        in version 1.1.1
        """
        self.name = simplify_whitespace(self.name)
        if not self.name:
            raise TracError(_("Invalid component name."))

        old = self._old.copy()
        with self.env.db_transaction as db:
            old_name = old['name']
            self.env.log.info("Updating component '%s'", self.name)
            db("""UPDATE component
                  SET name=%s, description=%s
                  WHERE name=%s
                  """, (self.name, self.description, old_name))
            self.checkin()

            if self.name != old_name:
                # Update component field in tickets
                self.env.log.info("Updating component field of all tickets "
                                  "associated with component '%s'", self.name)
                db("UPDATE ticket SET component=%s WHERE component=%s",
                   (self.name, old_name))
                TicketSystem(self.env).reset_ticket_fields()

                # Reparent attachments
                Attachment.reparent_all(self.env, 'component', old_name,
                                        'component', self.name)

        old_values = dict((k, v) for k, v in old.iteritems()
                          if getattr(self, k) != v)
        #for listener in TicketSystem(self.env).component_change_listeners:
        #    listener.component_changed(self, old_values)
        ResourceSystem(self.env).resource_changed(self, old_values)

    @classmethod
    def select(cls, env, db=None):
        """
        :since 1.0: the `db` parameter is no longer needed and will be removed
        in version 1.1.1
        """
        return ComponentCache(env).fetchall()
        
class VersionCache(core.Component):
    """Cache for version data and factory for 'version' resources."""

    @cached
    def versions(self):
        """Dictionary containing version data, indexed by name.

        Version data consist of a tuple containing the name, the
        datetime object for time and the description.
        """
        versions = {}
        for name, time, description in self.env.db_query("""
                SELECT name, time, description FROM version
                """):
            try:
                t = from_utimestamp(time) if time else None
            except:
                t = None
            versions[name] = (name,
                    t,
                    description or '')
        return versions

    def fetchone(self, name, version=None):
        """Retrieve an existing version having the given `name`.

        If `version` is specified, fill that instance instead of creating
        a fresh one.

        :return: `None` if no such version exists
        """
        data = self.versions.get(name)
        if data:
            return self.factory(data, version)

    def fetchall(self):
        """Iterator on all versions."""
        for data in self.versions.itervalues():
            yield self.factory(data)

    def factory(self, (name, time, description), version=None):
        """Build a `Version` object from version data.

        That instance remains *private*, i.e. can't be retrieved by
        name by other processes or even by other threads in the same
        process, until its `~Version.insert` method gets called with
        success.
        """
        version = version or BHVersion(self.env)
        version.name = name
        version.time = time
        version.description = description
        version.checkin(invalidate=False)
        return version
        
def group_versions(versions):
    """Wrap versions in a group, like what happens to versions."""
    return [(_('All'), versions)]

class BHVersion(object):
    def __init__(self, env, name=None, db=None):
        """Create an undefined version or fetch one from the database,
        if `name` is given.

        In the latter case however, raise `~trac.resource.ResourceNotFound`
        if a version of that name doesn't exist yet.
        """
        self.env = env
        if name:
            if not self.cache.fetchone(name, self):
                raise ResourceNotFound(
                    _("Version %(name)s does not exist.",
                      name=name), _("Invalid version name"))
        else:
            self.cache.factory((None, None, ''), self)

    @property
    def cache(self):
        return VersionCache(self.env)

    @property
    def resource(self):
        return Resource('version', self.name) ### .version !!!

    exists = property(lambda self: self._old['name'] is not None)

    def checkin(self, invalidate=True):
        self._old = {'name': self.name, 'description': self.description}
        if invalidate:
            del self.cache.versions

    _to_old = checkin #: compatibility with hacks < 0.12.5 (remove in 1.1.1)

    def delete(self, retarget_to=None, author=None, db=None):
        """Delete the version.

        :since 1.0: the `db` parameter is no longer needed and will be removed
        in version 1.1.1
        """
        with self.env.db_transaction as db:
            self.env.log.info("Deleting version %s", self.name)
            db("DELETE FROM version WHERE name=%s", (self.name,))

            # Retarget/reset tickets associated with this version
            now = datetime.now(utc)
            tkt_ids = [int(row[0]) for row in
                       db("SELECT id FROM ticket WHERE version=%s",
                          (self.name,))]
            for tkt_id in tkt_ids:
                ticket = Ticket(self.env, tkt_id, db)
                ticket['version'] = retarget_to
                comment = "Version %s deleted" % self.name # don't translate
                ticket.save_changes(author, comment, now)
            self._old['name'] = None
            del self.cache.versions
            TicketSystem(self.env).reset_ticket_fields()

        #for listener in TicketSystem(self.env).version_change_listeners:
        #    listener.version_deleted(self)
        ResourceSystem(self.env).resource_deleted(self)

    def insert(self, db=None):
        """Insert a new version.

        :since 1.0: the `db` parameter is no longer needed and will be removed
        in version 1.1.1
        """
        self.name = simplify_whitespace(self.name)
        if not self.name:
            raise TracError(_("Invalid version name."))

        with self.env.db_transaction as db:
            self.env.log.debug("Creating new version '%s'", self.name)
            db("""INSERT INTO version (name, time, description)
                  VALUES (%s,%s,%s)
                  """, (self.name, to_utimestamp(self.time), self.description))
            self.checkin()
            TicketSystem(self.env).reset_ticket_fields()

        #for listener in TicketSystem(self.env).version_change_listeners:
        #    listener.version_created(self)
        ResourceSystem(self.env).resource_created(self)

    def update(self, db=None):
        """Update the version.

        :since 1.0: the `db` parameter is no longer needed and will be removed
        in version 1.1.1
        """
        self.name = simplify_whitespace(self.name)
        if not self.name:
            raise TracError(_("Invalid version name."))

        old = self._old.copy()
        with self.env.db_transaction as db:
            old_name = old['name']
            self.env.log.info("Updating version '%s'", self.name)
            db("""UPDATE version
                  SET name=%s, time=%s, description=%s
                  WHERE name=%s
                  """, (self.name, to_utimestamp(self.time),
                        self.description, old_name))
            self.checkin()

            if self.name != old_name:
                # Update version field in tickets
                self.env.log.info("Updating version field of all tickets "
                                  "associated with version '%s'", self.name)
                db("UPDATE ticket SET version=%s WHERE version=%s",
                   (self.name, old_name))
                TicketSystem(self.env).reset_ticket_fields()

                # Reparent attachments
                Attachment.reparent_all(self.env, 'version', old_name,
                                        'version', self.name)

        old_values = dict((k, v) for k, v in old.iteritems()
                          if getattr(self, k) != v)
        #for listener in TicketSystem(self.env).version_change_listeners:
        #    listener.version_changed(self, old_values)
        ResourceSystem(self.env).resource_changed(self, old_values)

    @classmethod
    def select(cls, env, db=None):
        """
        :since 1.0: the `db` parameter is no longer needed and will be removed
        in version 1.1.1
        """
        return VersionCache(env).fetchall()