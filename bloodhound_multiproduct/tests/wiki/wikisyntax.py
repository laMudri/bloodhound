
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

"""Tests for inherited Apache(TM) Bloodhound's wiki syntax 
in product environments"""

import os.path
import re
import unittest

from trac.wiki.tests import wikisyntax

from multiproduct.env import ProductEnvironment
from tests.env import MultiproductTestCase
from tests.wiki import formatter

def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(formatter.test_suite(wikisyntax.TEST_CASES, 
                                  wikisyntax.wiki_setup, wikisyntax.__file__,
                                  wikisyntax.wiki_teardown))
    suite.addTest(formatter.test_suite(wikisyntax.RELATIVE_LINKS_TESTS, 
                                  wikisyntax.wiki_setup, wikisyntax.__file__,
                                  wikisyntax.wiki_teardown,
                                  context=('wiki', 'Main/Sub')))
    suite.addTest(formatter.test_suite(wikisyntax.SPLIT_PAGE_NAMES_TESTS, 
                                  wikisyntax.wiki_setup_split, 
                                  wikisyntax.__file__, 
                                  wikisyntax.wiki_teardown,
                                  context=('wiki', 'Main/Sub')))
    suite.addTest(formatter.test_suite(wikisyntax.SCOPED_LINKS_TESTS, 
                                  wikisyntax.wiki_setup, wikisyntax.__file__,
                                  wikisyntax.wiki_teardown,
                                  context=('wiki',
                                      'FirstLevel/SecondLevel/ThirdLevel')))
    return suite


if __name__ == '__main__':
    unittest.main(defaultTest='test_suite')

