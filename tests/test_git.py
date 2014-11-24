# -*- coding: utf-8 -*-
import os
import shutil
from sh import git
from mock import patch
from django.test import TestCase
from django.core.urlresolvers import reverse
from django.utils.translation import ugettext_lazy as _
from waliki.models import Page
from waliki.git import Git
from waliki.settings import WALIKI_DATA_DIR, WALIKI_COMMITTER_EMAIL, WALIKI_COMMITTER_NAME
from .factories import PageFactory


class TestGit(TestCase):

    def setUp(self):
        self.page = PageFactory()
        self.edit_url = reverse('waliki_edit', args=(self.page.slug,))

    def test_init_git_create_repo(self):
        git_dir = os.path.join(WALIKI_DATA_DIR, '.git')
        shutil.rmtree(git_dir)
        Git()
        self.assertTrue(os.path.isdir(git_dir))
        self.assertEqual(git.config('user.name').stdout.decode('utf8')[:-1], WALIKI_COMMITTER_NAME)
        self.assertEqual(git.config('user.email').stdout.decode('utf8')[:-1], WALIKI_COMMITTER_EMAIL)


    def test_commit_existent_page_with_no_previous_commits(self):
        response = self.client.get(self.edit_url)
        data = response.context[0]['form'].initial
        data["raw"] = "test content"
        data["message"] = "testing :)"
        response = self.client.post(self.edit_url, data)
        self.assertEqual(self.page.raw, "test content")
        self.assertEqual(Git().version(self.page, 'HEAD'), "test content")
        self.assertIn("testing :)", git.log('-s', '--format=%s', self.page.path))

    def test_commit_existent_page_with_previous_commits(self):
        self.page.raw = "lala"
        Git().commit(self.page, message="previous commit")
        assert "previous commit" in git.log('-s', '--format=%s', self.page.path)
        response = self.client.get(self.edit_url)
        data = response.context[0]['form'].initial
        data["raw"] = "test content"
        response = self.client.post(self.edit_url, data)
        self.assertEqual(self.page.raw, "test content")
        self.assertEqual(Git().version(self.page, 'HEAD'), "test content")

    def test_commit_new_page(self):
        assert not Page.objects.filter(slug='test').exists()
        url = reverse('waliki_edit', args=('test',))
        response = self.client.post(url)
        # it exists now
        self.assertTrue(Page.objects.filter(slug='test').exists())
        data = response.context[0]['form'].initial
        data["raw"] = "hey there\n"
        data["title"] = "Test Page"
        data["message"] = ""
        response = self.client.post(url, data)
        page = Page.objects.get(slug='test')
        self.assertEqual(page.title, "Test Page")
        self.assertEqual(page.raw, "hey there\n")
        self.assertEqual(Git().version(page, 'HEAD'), "hey there\n")

    def test_concurrent_edition_with_no_conflict(self):
        self.page.raw = "\n- item1\n"
        Git().commit(self.page, message="original")

        response1 = self.client.get(self.edit_url)
        response2 = self.client.get(self.edit_url)

        data1 = response1.context[0]['form'].initial
        data1["raw"] = self.page.raw + '\n- item2'
        data1["message"] = "add item2"

        data2 = response2.context[0]['form'].initial
        data2["raw"] = '- item0\n' + self.page.raw
        data1["message"] = "add item0"
        self.client.post(self.edit_url, data1)
        with patch('waliki.views.messages') as messages:
            self.client.post(self.edit_url, data2)
        # there is a message
        self.assertTrue(messages.warning.called)
        self.assertIn('There were changes', messages.warning.call_args[0][1])

        # newer versions of git don't leave a blank line at the end
        self.assertRegexpMatches("- item0\n\n- item1\n\n- item2\n?$", self.page.raw)

    def test_concurrent_edition_with_conflict(self):
        self.page.raw = "- item1"
        Git().commit(self.page, message="original")
        response1 = self.client.get(self.edit_url)
        response2 = self.client.get(self.edit_url)

        data1 = response1.context[0]['form'].initial
        data1["raw"] = '- item2'
        data1["message"] = "add item2"

        data2 = response2.context[0]['form'].initial
        data2["raw"] = '- item0'
        data1["message"] = "add item0"
        r = self.client.post(self.edit_url, data1)
        self.assertRedirects(r, reverse('waliki_detail', args=(self.page.slug,)))
        r = self.client.post(self.edit_url, data2)
        self.assertRedirects(r, self.edit_url)

        self.assertEqual('', git.status('--porcelain', self.page.path).stdout.decode('utf8'))
        self.assertIn('Merged with conflict', git.log("--no-color", "--pretty=format:%s", "-n 1", self.page.path).stdout.decode('utf8'))
        self.assertRegexpMatches(self.page.raw,'<<<<<<< HEAD\n- item2\n=======\n- item0\n>>>>>>> [0-9a-f]{7}\n')

        # can edit in conflict
        response = self.client.get(self.edit_url)
        data1 = response.context[0]['form'].initial
        data1["raw"] = '- item0\n- item2'
        data1["message"] = "fixing :)"
        response = self.client.post(self.edit_url, data1)
        self.assertRedirects(response, reverse('waliki_detail', args=(self.page.slug,)))
        self.assertEqual(self.page.raw, '- item0\n- item2')

    def test_concurrent_edition_no_existent_page(self):
        assert not Page.objects.filter(slug='test2').exists()
        url = reverse('waliki_edit', args=('test2',))
        response1 = self.client.post(url)
        response2 = self.client.post(url)
        page = Page.objects.get(slug='test2')

        data1 = response1.context[0]['form'].initial
        data1["raw"] = '- item2\n'
        data1["message"] = "add item2"
        data1["title"] = "a title"

        data2 = response2.context[0]['form'].initial
        data2["raw"] = '- item0\n'
        data2["message"] = "add item0"
        data2["title"] = "another title"

        self.client.post(url, data1)
        with patch('waliki.views.messages') as messages:
            response = self.client.post(url, data2)
        # there is a warning
        self.assertTrue(messages.warning.called)
        self.assertIsInstance(messages.warning.call_args[0][1], Page.EditionConflict)

        # redirect
        self.assertRedirects(response, url)

        # file committed with conflict
        self.assertEqual('', git.status('--porcelain', page.path).stdout.decode('utf8'))
        self.assertIn('Merged with conflict', git.log("--pretty=format:%s", "-n 1", page.path).stdout.decode('utf8'))

        self.assertRegexpMatches(page.raw, r"""<<<<<<< HEAD\n- item2
=======\n- item0\n>>>>>>> [0-9a-f]{7}\n""")
        page = Page.objects.get(slug='test2')       # refresh
        self.assertEqual(page.title, "another title")

        # can edit in conflict
        response = self.client.get(url)

        data1 = response.context[0]['form'].initial
        data1["raw"] = '- item0\n- item2\n'
        data1["message"] = "fixing :)"
        response = self.client.post(url, data1)
        self.assertRedirects(response, reverse('waliki_detail', args=(page.slug,)))
        self.assertEqual(page.raw, '- item0\n- item2\n')

    def test_unicode_content(self):
        response = self.client.get(self.edit_url)
        data = response.context[0]['form'].initial
        data['raw'] = u'Ω'
        data['message'] = 'testing :)'
        response = self.client.post(self.edit_url, data)
        self.assertRedirects(response, reverse('waliki_detail', args=(self.page.slug,)))
        self.assertEqual(Git().version(self.page, 'HEAD'), u'Ω')

    def test_commit_page_with_no_changes(self):
        self.page.raw = 'lala'
        Git().commit(self.page, message='previous commit')

        response = self.client.get(self.edit_url)
        data = response.context[0]['form'].initial
        data['raw'] = self.page.raw
        data['message'] = 'testing :)'
        response = self.client.post(self.edit_url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['form'].is_valid())
        self.assertIn('raw', response.context['form'].errors)
        self.assertContains(response, _('There were no changes in the page to commit.'))

        self.assertEqual(self.page.raw, 'lala')
        self.assertEqual(Git().version(self.page, 'HEAD'), 'lala')


    def test_history_log(self):
        self.page.raw = 'line\n' * 10
        Git().commit(self.page, message=u'"10 lines ñoñas"')       # include quotes and non ascii

        self.page.raw = 'another line2\n' * 2
        Git().commit(self.page, message=u'changed 20%')             # include quotes and non ascii

        response = self.client.get(reverse('waliki_history', args=(self.page.slug,)))
        self.assertEqual(response.status_code, 200)

        history = response.context[0].get('history')

        self.assertEqual(history[0]['message'], u'changed 20%')
        self.assertEqual(history[0]['deletion'], 10)
        self.assertEqual(history[0]['insertion'], 2)
        self.assertTrue(str(history[0]['insertion_relative']).startswith('16.6'))
        self.assertEqual(history[1]['message'], u'"10 lines ñoñas"')

    def test_whatchanged(self):
        self.page.raw = 'line\n'
        Git().commit(self.page, message=u'"//"')
        another_page = PageFactory(path='another-page.rst')
        another_page.raw = "hello!"
        Git().commit(another_page, message=u'hello history')
        response = self.client.get(reverse('waliki_whatchanged'))
        changes = response.context[0]['changes']

        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]['page'], another_page)
        self.assertEqual(changes[1]['page'], self.page)
        self.assertEqual(changes[0]['message'], 'hello history')
        self.assertEqual(changes[1]['message'], '"//"')

    def test_whatchanged_multiples_files_in_one_commit(self):
        git_dir = os.path.join(WALIKI_DATA_DIR, '.git')
        shutil.rmtree(git_dir)
        Git()

        self.page.raw = 'line\n'
        another_page = PageFactory(path='another-page.rst')
        another_page.raw = "hello!"
        git.add('.')
        git.commit('-am', 'commit all')
        response = self.client.get(reverse('waliki_whatchanged'))
        changes = response.context[0]['changes']
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]['page'], another_page)
        self.assertEqual(changes[1]['page'], self.page)
        self.assertEqual(changes[0]['message'], 'commit all')
        self.assertEqual(changes[1]['message'], 'commit all')
