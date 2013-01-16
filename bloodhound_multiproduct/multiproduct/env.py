
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

"""Bloodhound product environment and related APIs"""

import os.path
from urlparse import urlsplit

from trac.config import ConfigSection, Option
from trac.core import Component, ComponentManager, implements
from trac.db.api import with_transaction, TransactionContextManager, QueryContextManager
from trac.util import get_pkginfo, lazy
from trac.util.compat import sha1
from trac.versioncontrol import RepositoryManager
from trac.web.href import Href

from multiproduct.model import Product
from multiproduct.dbcursor import BloodhoundIterableCursor

import trac.env

class Environment(trac.env.Environment):
    """Bloodhound environment manager

    This class is intended as monkey-patch replacement for
    trac.env.Environment. Required database access methods/properties
    are replaced to provide global view of the database in contrast
    to ProductEnvironment that features per-product view of the database
    (in the context of selected product).
    """
    def __init__(self, path, create=False, options=[]):
        # global environment w/o parent, set these two before super.__init__
        # as database access can take place within trac.env.Environment
        self.parent = None
        self.product = None
        super(Environment, self).__init__(path, create=create, options=options)

    @property
    def db_query(self):
        BloodhoundIterableCursor.set_env(self)
        return super(Environment, self).db_query

    @property
    def db_transaction(self):
        BloodhoundIterableCursor.set_env(self)
        return super(Environment, self).db_transaction

# replace trac.env.Environment with Environment
trac.env.Environment = Environment

# this must follow the monkey patch (trac.env.Environment) above, otherwise
# trac.test.EnvironmentStub will not be correct as the class will derive from
# not replaced trac.env.Environment
import trac.test

class EnvironmentStub(trac.test.EnvironmentStub):
    """Bloodhound test environment stub

    This class replaces trac.test.EnvironmentStub and extends it with parent and product
    properties (same case as with the Environment).
    """
    def __init__(self, default_data=False, enable=None, disable=None,
                 path=None, destroying=False):
        self.parent = None
        self.product = None
        super(EnvironmentStub, self).__init__(default_data=default_data,
                                              enable=enable, disable=disable,
                                              path=path, destroying=destroying)

    @property
    def db_query(self):
        BloodhoundIterableCursor.set_env(self)
        return super(EnvironmentStub, self).db_query

    @property
    def db_transaction(self):
        BloodhoundIterableCursor.set_env(self)
        return super(EnvironmentStub, self).db_transaction

# replace trac.test.EnvironmentStub
trac.test.EnvironmentStub = EnvironmentStub

class ProductEnvironment(Component, ComponentManager):
    """Bloodhound product-aware environment manager.

    Bloodhound encapsulates access to product resources stored inside a
    Trac environment via product environments. They are compatible lightweight
    irepresentations of top level environment. 

    Product environments contain among other things:

    * a configuration file, 
    * product-aware clones of the wiki and ticket attachments files,

    Product environments do not have:

    * product-specific templates and plugins,
    * a separate database
    * active participation in database upgrades and other setup tasks

    See https://issues.apache.org/bloodhound/wiki/Proposals/BEP-0003
    """

    implements(trac.env.ISystemInfoProvider)

    def __getattr__(self, attrnm):
        """Forward attribute access request to parent environment.

        Initially this will affect the following members of
        `trac.env.Environment` class:

        system_info_providers, secure_cookies, project_admin_trac_url,
        get_system_info, get_version, get_templates_dir, get_templates_dir,
        get_log_dir, backup
        """
        try:
            if attrnm == 'parent':
                raise AttributeError
            return getattr(self.parent, attrnm)
        except AttributeError:
            raise AttributeError("'%s' object has no attribute '%s'" %
                    (self.__class__.__name__, attrnm))

    @property
    def setup_participants(self):
        """Setup participants list for product environments will always
        be empty based on the fact that upgrades will only be handled by
        the global environment.
        """
        return ()

    components_section = ConfigSection('components',
        """This section is used to enable or disable components
        provided by plugins, as well as by Trac itself.

        See also: TracIni , TracPlugins
        """)

    @property
    def shared_plugins_dir():
        """Product environments may not add plugins.
        """
        return ''

    # TODO: Estimate product base URL considering global base URL, pattern, ...
    base_url = ''

    # TODO: Estimate product base URL considering global base URL, pattern, ...
    base_url_for_redirect = ''

    @property
    def project_name(self):
        """Name of the product.
        """
        return self.product.name

    @property
    def project_description(self):
        """Short description of the product.
        """
        return self.product.description

    @property
    def project_url(self):
        """URL of the main project web site, usually the website in
        which the `base_url` resides. This is used in notification
        e-mails.
        """
        # FIXME: Should products have different values i.e. config option ?
        return self.parent.project_url

    project_admin = Option('project', 'admin', '',
        """E-Mail address of the product's leader / administrator.""")

    @property
    def project_footer(self):
        """Page footer text (right-aligned).
        """
        # FIXME: Should products have different values i.e. config option ?
        return self.parent.project_footer

    project_icon = Option('project', 'icon', 'common/trac.ico',
        """URL of the icon of the product.""")

    log_type = Option('logging', 'log_type', 'inherit',
        """Logging facility to use.

        Should be one of (`inherit`, `none`, `file`, `stderr`, 
        `syslog`, `winlog`).""")

    log_file = Option('logging', 'log_file', 'trac.log',
        """If `log_type` is `file`, this should be a path to the
        log-file.  Relative paths are resolved relative to the `log`
        directory of the environment.""")

    log_level = Option('logging', 'log_level', 'DEBUG',
        """Level of verbosity in log.

        Should be one of (`CRITICAL`, `ERROR`, `WARN`, `INFO`, `DEBUG`).""")

    log_format = Option('logging', 'log_format', None,
        """Custom logging format.

        If nothing is set, the following will be used:

        Trac[$(module)s] $(levelname)s: $(message)s

        In addition to regular key names supported by the Python
        logger library (see
        http://docs.python.org/library/logging.html), one could use:

        - $(path)s     the path for the current environment
        - $(basename)s the last path component of the current environment
        - $(project)s  the project name

        Note the usage of `$(...)s` instead of `%(...)s` as the latter form
        would be interpreted by the ConfigParser itself.

        Example:
        `($(thread)d) Trac[$(basename)s:$(module)s] $(levelname)s: $(message)s`

        ''(since 0.10.5)''""")

    def __init__(self, env, product):
        """Initialize the product environment.

        :param env:     the global Trac environment
        :param product: product prefix or an instance of
                        multiproduct.model.Product
        """
        if not isinstance(env, trac.env.Environment):
            cls = self.__class__
            raise TypeError("Initializer must be called with " \
                "trac.env.Environment instance as first argument " \
                "(got %s instance instead)" % 
                         (cls.__module__ + '.' + cls.__name__, ))

        ComponentManager.__init__(self)

        if isinstance(product, Product):
            if product._env is not env:
                raise ValueError("Product's environment mismatch")
        elif isinstance(product, basestring):
            products = Product.select(env, where={'prefix': product})
            if len(products) == 1 :
                product = products[0]
            else:
                env.log.debug("Products for '%s' : %s",
                        product, products)
                raise LookupError("Missing product %s" % (product,))

        self.parent = env
        self.product = product
        self.path = self.parent.path
        self.systeminfo = []
        self._href = self._abs_href = None

        self.setup_config()

    # ISystemInfoProvider methods

    # Same as parent environment's . Avoid duplicated code
    component_activated = trac.env.Environment.component_activated.im_func
    _component_name = trac.env.Environment._component_name.im_func
    _component_rules = trac.env.Environment._component_rules
    enable_component = trac.env.Environment.enable_component.im_func
    get_known_users = trac.env.Environment.get_known_users.im_func
    get_repository = trac.env.Environment.get_repository.im_func
    is_component_enabled = trac.env.Environment.is_component_enabled.im_func

    def get_db_cnx(self):
        """Return a database connection from the connection pool

        :deprecated: Use :meth:`db_transaction` or :meth:`db_query` instead

        `db_transaction` for obtaining the `db` database connection
        which can be used for performing any query
        (SELECT/INSERT/UPDATE/DELETE)::

           with env.db_transaction as db:
               ...


        `db_query` for obtaining a `db` database connection which can
        be used for performing SELECT queries only::

           with env.db_query as db:
               ...
        """
        # share connection pool with global environment
        return self.parent.get_db_cnx()

    @lazy
    def db_exc(self):
        """Return an object (typically a module) containing all the
        backend-specific exception types as attributes, named
        according to the Python Database API
        (http://www.python.org/dev/peps/pep-0249/).

        To catch a database exception, use the following pattern::

            try:
                with env.db_transaction as db:
                    ...
            except env.db_exc.IntegrityError, e:
                ...
        """
        # exception types same as in global environment
        return self.parent.db_exc()

    def with_transaction(self, db=None):
        """Decorator for transaction functions :deprecated:"""
        return with_transaction(self, db)

    def get_read_db(self):
        """Return a database connection for read purposes :deprecated:

        See `trac.db.api.get_read_db` for detailed documentation."""
        # database connection is shared with global environment
        return self.parent.get_read_db()

    @property
    def db_query(self):
        """Return a context manager which can be used to obtain a
        read-only database connection.

        Example::

            with env.db_query as db:
                cursor = db.cursor()
                cursor.execute("SELECT ...")
                for row in cursor.fetchall():
                    ...

        Note that a connection retrieved this way can be "called"
        directly in order to execute a query::

            with env.db_query as db:
                for row in db("SELECT ..."):
                    ...

        If you don't need to manipulate the connection itself, this
        can even be simplified to::

            for row in env.db_query("SELECT ..."):
                ...

        :warning: after a `with env.db_query as db` block, though the
          `db` variable is still available, you shouldn't use it as it
          might have been closed when exiting the context, if this
          context was the outermost context (`db_query` or
          `db_transaction`).
        """
        BloodhoundIterableCursor.set_env(self)
        return QueryContextManager(self.parent)

    @property
    def db_transaction(self):
        """Return a context manager which can be used to obtain a
        writable database connection.

        Example::

            with env.db_transaction as db:
                cursor = db.cursor()
                cursor.execute("UPDATE ...")

        Upon successful exit of the context, the context manager will
        commit the transaction. In case of nested contexts, only the
        outermost context performs a commit. However, should an
        exception happen, any context manager will perform a rollback.

        Like for its read-only counterpart, you can directly execute a
        DML query on the `db`::

            with env.db_transaction as db:
                db("UPDATE ...")

        If you don't need to manipulate the connection itself, this
        can also be simplified to::

            env.db_transaction("UPDATE ...")

        :warning: after a `with env.db_transaction` as db` block,
          though the `db` variable is still available, you shouldn't
          use it as it might have been closed when exiting the
          context, if this context was the outermost context
          (`db_query` or `db_transaction`).
        """
        BloodhoundIterableCursor.set_env(self)
        return TransactionContextManager(self.parent)

    def shutdown(self, tid=None):
        """Close the environment."""
        RepositoryManager(self).shutdown(tid)
        # FIXME: Shared DB so IMO this should not happen ... at least not here
        #DatabaseManager(self).shutdown(tid)
        if tid is None:
            self.log.removeHandler(self._log_handler)
            self._log_handler.flush()
            self._log_handler.close()
            del self._log_handler

    def create(self, options=[]):
        """Placeholder for compatibility when trying to create the basic 
        directory structure of the environment, etc ...

        This method does nothing at all.
        """
        # TODO: Handle options args

    def setup_config(self):
        """Load the configuration object.
        """
        # FIXME: Install product-specific configuration object
        self.config = self.parent.config
        self.setup_log()

    def setup_log(self):
        """Initialize the logging sub-system."""
        from trac.log import logger_handler_factory
        logtype = self.log_type
        self.parent.log.debug("Log type '%s' for product '%s'", 
                logtype, self.product.prefix)
        if logtype == 'inherit':
            logtype = self.parent.log_type
            logfile = self.parent.log_file
            format = self.parent.log_format
        else:
            logfile = self.log_file
            format = self.log_format
        if logtype == 'file' and not os.path.isabs(logfile):
            logfile = os.path.join(self.get_log_dir(), logfile)
        logid = 'Trac.%s.%s' % \
                (sha1(self.parent.path).hexdigest(), self.product.prefix)
        if format:
            format = format.replace('$(', '%(') \
                     .replace('%(path)s', self.path) \
                     .replace('%(basename)s', os.path.basename(self.path)) \
                     .replace('%(project)s', self.project_name)
        self.log, self._log_handler = logger_handler_factory(
            logtype, logfile, self.log_level, logid, format=format)

        from trac import core, __version__ as VERSION
        self.log.info('-' * 32 + ' environment startup [Trac %s] ' + '-' * 32,
                      get_pkginfo(core).get('version', VERSION))

    def needs_upgrade(self):
        """Return whether the environment needs to be upgraded."""
        return False

    def upgrade(self, backup=False, backup_dest=None):
        """Upgrade database.

        :param backup: whether or not to backup before upgrading
        :param backup_dest: name of the backup file
        :return: whether the upgrade was performed
        """
        # (Database) upgrades handled by global environment
        # FIXME: True or False ?
        return True

    @property
    def href(self):
        """The application root path"""
        if not self._href:
            self._href = Href(urlsplit(self.abs_href.base)[2])
        return self._href

    @property
    def abs_href(self):
        """The application URL"""
        if not self._abs_href:
            if not self.base_url:
                self.log.warn("base_url option not set in configuration, "
                              "generated links may be incorrect")
                self._abs_href = Href('')
            else:
                self._abs_href = Href(self.base_url)
        return self._abs_href

