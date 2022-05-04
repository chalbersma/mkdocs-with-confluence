import time
import os
import os.path
import re
import tempfile
import shutil
import requests
import mimetypes
import mistune
import logging

from time import sleep
from os import environ
from mkdocs.config import config_options
from mkdocs.plugins import BasePlugin
from mkdocs.utils import warning_filter
from md2cf.confluence_renderer import ConfluenceRenderer
from atlassian import Confluence

import atlassian.errors

TEMPLATE_BODY = "<p> TEMPLATE </p>"


class MkdocsWithConfluence(BasePlugin):
    _id = 0
    config_scheme = (
        ("host_url", config_options.Type(str, default=None)),
        ("space", config_options.Type(str, default=None)),
        ("parent_page_name", config_options.Type(str, default=None)),
        ("username", config_options.Type(str, default=environ.get("JIRA_USERNAME", None))),
        ("password", config_options.Type(str, default=environ.get("JIRA_PASSWORD", None))),
        ("cloud", config_options.Type(bool, default=False)),
        ("enabled_if_env", config_options.Type(str, default=None)),
        ("verbose", config_options.Type(bool, default=False)),
        ("debug", config_options.Type(bool, default=False)),
        ("dryrun", config_options.Type(bool, default=False)),
    )

    def __init__(self):
        self.enabled = True
        self.confluence_renderer = ConfluenceRenderer(use_xhtml=True)
        self.confluence_mistune = mistune.Markdown(renderer=self.confluence_renderer)
        self.simple_log = False
        self.flen = 1
        self.logger = logging.getLogger(f"mkdocs.plugins.{__name__}")
        self.logger.addFilter(warning_filter)



    def on_nav(self, nav, config, files):
        MkdocsWithConfluence.tab_nav = []
        navigation_items = nav.__repr__()
        for n in navigation_items.split("\n"):
            # print(f"* {n}")
            leading_spaces = len(n) - len(n.lstrip(" "))
            spaces = leading_spaces * " "
            if "Page" in n:
                try:
                    self.page_title = self.__get_page_title(n)
                    if self.page_title is None:
                        raise AttributeError
                except AttributeError:
                    self.page_local_path = self.__get_page_url(n)
                    self.logger.info(
                        f"Page from path {self.page_local_path} has no "
                        f"entity in the mkdocs.yml nav section. It will be uploaded "
                        f"to Confluence, but you may not see it on the web server!"
                    )
                    self.page_local_name = self.__get_page_name(n)
                    self.page_title = self.page_local_name

                p = spaces + self.page_title
                MkdocsWithConfluence.tab_nav.append(p)
            if "Section" in n:
                s = spaces + self.__get_section_title(n)
                MkdocsWithConfluence.tab_nav.append(s)

    def on_files(self, files, config):
        pages = files.documentation_pages()
        try:
            self.flen = len(pages)
            self.logger.debug(f"Number of Files in directory tree: {self.flen}")
        except 0:
            self.logger.error("ERR: You have no documentation pages" "in the directory tree, please add at least one!")
    def on_post_template(self, output_content, template_name, config):
        if self.config["verbose"] is False and self.config["debug"] is False:
            self.simple_log = True
            self.logger.info("Mkdocs With Confluence: Start exporting markdown pages... (simple logging)")
        else:
            self.simple_log = False

    def on_config(self, config):
        if "enabled_if_env" in self.config:
            env_name = self.config["enabled_if_env"]
            if env_name:
                self.enabled = os.environ.get(env_name) == "1"
                if not self.enabled:
                    self.logger.warn(
                        "Mkdocs With Confluence: Exporting MKDOCS pages to Confluence turned OFF: "
                        f"(set environment variable {env_name} to 1 to enable)"
                    )
                    return
                else:
                    self.logger.info(
                        "Mkdocs With Confluence: Exporting MKDOCS pages to Confluence "
                        f"turned ON by var {env_name}==1!"
                    )
                    self.enabled = True
            else:
                self.logger.warn(
                    "Mkdocs With Confluence: Exporting MKDOCS pages to Confluence turned OFF: "
                    f"(set environment variable {env_name} to 1 to enable)"
                )
                return
        else:
            self.logger.info("INFO    -  Mkdocs With Confluence: Exporting MKDOCS pages to Confluence turned ON by default!")
            self.enabled = True

        if self.config["dryrun"]:
            self.logger.info("WARNING -  Mkdocs With Confluence - DRYRUN MODE turned ON")
            self.dryrun = True
        else:
            self.dryrun = False

        self.confluence = Confluence(
            url=self.config["host_url"],
            username=self.config["username"],
            password=self.config["password"],
            cloud=self.config["cloud"]
        )

    def on_page_markdown(self, markdown, page, config, files):
        MkdocsWithConfluence._id += 1
        self.pw = self.config["password"]
        self.user = self.config["username"]

        if self.enabled:
            if self.simple_log is True:
                self.logger.info("Mkdocs With Confluence: Page export progress: [", end="", flush=True)
                for i in range(MkdocsWithConfluence._id):
                    self.logger.info("#", end="", flush=True)
                for j in range(self.flen - MkdocsWithConfluence._id):
                    self.logger.info("-", end="", flush=True)
                self.logger.info(f"] ({MkdocsWithConfluence._id} / {self.flen})", end="\r", flush=True)

            self.logger.info(f"Handling Page '{page.title}' (And Parent Nav Pages if necessary):\n")
            if not all(self.config_scheme):
                self.logger.error("  You have empty values in your config. Aborting.")
                return markdown

            try:
                self.logger.debug("  Get section first parent title...")
                try:
                    parent = self.__get_section_title(page.ancestors[0].__repr__())
                except IndexError as e:
                    self.logger.debug(
                        f'  ERR({e}): No second parent! Assuming {self.config["parent_page_name"]}'
                    )
                    parent = None
                self.logger.debug(f"  {parent}")
                if not parent:
                    parent = self.config["parent_page_name"]

                if self.config["parent_page_name"] is not None:
                    main_parent = self.config["parent_page_name"]
                else:
                    main_parent = self.config["space"]

                self.logger.debug("  Get section second parent title")
                try:
                    parent1 = self.__get_section_title(page.ancestors[1].__repr__())
                except IndexError as e:
                    self.logger.debug(f"  No second parent. Assuming second parent is main parent: '{main_parent}'")
                    parent1 = None

                if not parent1:
                    parent1 = main_parent
                    self.logger.debug(f"  Only one parent found. Assuming as a first node after main parent config '{main_parent}'")
                    self.logger.debug(f"  PARENT0: '{parent}', PARENT1: '{parent1}', MAIN PARENT: '{main_parent}'")

                tf = tempfile.NamedTemporaryFile(delete=False)
                f = open(tf.name, "w")

                new_markdown = re.sub(
                    r'<img src="file:///tmp/', '<p><ac:image ac:height="350"><ri:attachment ri:filename="', markdown
                )
                new_markdown = re.sub(r'" style="page-break-inside: avoid;">', '"/></ac:image></p>', new_markdown)
                confluence_body = self.confluence_mistune(new_markdown)
                f.write(confluence_body)
                page_name = page.title
                new_name = "confluence_page_" + page_name.replace(" ", "_") + ".html"
                shutil.copy(f.name, new_name)
                f.close()

                embedded_files = []
                for match in re.finditer(r'<ri:attachment ri:filename="([^\"]+)"', confluence_body):
                    embedded_files.append(match.group(1))

                attachments = []
                for embedded_file in embedded_files:
                    for file in files:
                        if os.path.basename(file.src_path) == embedded_file:
                            attachments.append(os.path.join(config['docs_dir'], file.src_path))

                if self.config["debug"]:
                    self.logger.debug(
                        f"  Uploading page to Confluence:\n"
                        f"  HOST: {self.config['host_url']}\n"
                        f"  SPACE: {self.config['space']}\n"
                        f"  TITLE: {page.title}\n"
                        f"  PARENT: {parent}\n"
                        f"  BODY: {confluence_body}\n"
                    )

                page_id = self.find_page_id(page.title)
                if page_id is not None:
                    self.logger.debug(
                        f"  About to update page '{page.title}': "
                        f"checking if parent page on confluence is the same as here..."
                    )

                    parent_name = self.find_parent_name_of_page(page.title)

                    if parent_name == parent:
                        self.logger.debug("  OK, Parents match. Continue...")
                    else:
                        self.logger.error(f"  Parents does not match: '{parent}' != '{parent_name}'. Aborting...")
                        return markdown
                    self.update_page(page.title, confluence_body)
                    for i in MkdocsWithConfluence.tab_nav:
                        if page.title in i:
                            self.logger.info(f"  *UPDATE*: {i}")
                else:
                    self.logger.debug(f"  Page '{page.title}' not found on Confluence. Creating...")
                    self.logger.debug(f"  Page: {page.title}, PARENT0: {parent}, PARENT1: {parent1}, MAIN PARENT: {main_parent}")
                    self.logger.debug(f"  Querying Confluence for parent ({parent}) page id...")
                    parent_id = self.find_page_id(parent)
                    self.wait_until(parent_id, 1, 20)
                    self.logger.debug(f"  Querying Confluence for parent1 ({parent1}) page id...")
                    second_parent_id = self.find_page_id(parent1)
                    self.wait_until(second_parent_id, 1, 20)
                    self.logger.debug(f"  Querying Confluence for main_parent ({main_parent}) page id...")
                    main_parent_id = self.find_page_id(main_parent)
                    if not parent_id:
                        if not second_parent_id:
                            main_parent_id = self.find_page_id(main_parent)
                            if not main_parent_id:
                                self.logger.error("Main parent unknwon. Aborting!")
                                return markdown

                            self.logger.debug(f"  Trying to ADD page '{parent1}' to main parent({main_parent}) ID: {main_parent_id}")
                            body = TEMPLATE_BODY.replace("TEMPLATE", parent1)
                            self.add_page(parent1, main_parent_id, body)
                            for i in MkdocsWithConfluence.tab_nav:
                                if parent1 in i:
                                    self.logger.info(f"  *NEW PAGE*: {i}")
                            time.sleep(1)

                        self.logger.debug(f"  Trying to ADD page '{parent}' to parent1({parent1}) ID: {second_parent_id}")
                        body = TEMPLATE_BODY.replace("TEMPLATE", parent)
                        self.add_page(parent, second_parent_id, body)
                        for i in MkdocsWithConfluence.tab_nav:
                            if parent in i:
                                self.logger.info(f"  *NEW PAGE*: {i}")
                        time.sleep(1)

                    parent_id = self.find_page_id(parent)
                    self.logger.debug(f"  Trying to ADD page '{page.title}' to parent0({parent}) ID: {parent_id}")
                    if parent_id is not None:
                        self.add_page(page.title, parent_id, confluence_body)
                    else:
                        self.logger.error(f"  Parent ID not found. Aborting...")
                        return markdown
                        
                    for i in MkdocsWithConfluence.tab_nav:
                        if page.title in i:
                            self.logger.info(f"  *NEW PAGE*: {i}")

                if attachments:
                    self.logger.info(f"  Uploading {len(attachments)} attachments...")
                    for f in attachments:
                        self.logger.debug(f"    Uploading {f}")
                        self.add_attachment(page.title, f)

            except IndexError as e:
                self.logger.error(f"  ERR({e}): Exception error!")
                return markdown

        return markdown

    def on_page_content(self, html, page, config, files):
        return html

    def __get_page_url(self, section):
        return re.search("url='(.*)'\\)", section).group(1)[:-1] + ".md"

    def __get_page_name(self, section):
        return os.path.basename(re.search("url='(.*)'\\)", section).group(1)[:-1])

    def __get_section_title(self, section):
        return re.search("Section\\(title='(.*)'\\)", section).group(1)

    def __get_page_title(self, section):
        r = re.search("\\s*Page\\(title='(.*)',", section)
        try:
            return r.group(1)
        except AttributeError:
            name = self.__get_page_url(section)
            self.logger.error(f"Page '{name}' doesn't exist in the mkdocs.yml nav section!")

    def add_attachment(self, page_name, filepath):
        self.logger.info(f"  {page_name} *NEW ATTACHMENT* {filepath}")

        page_id = self.find_page_id(page_name)

        if not self.dryrun:

            self.confluence.attach_file(filepath,
                                        name=os.path.basename(filepath),
                                        page_id=page_id,
                                        space=self.config["space"])



        else:
            self.logger.info(f"Not adding attachment {filepath}, dryrun")

    def find_page_id(self, page_name):

        try:
            page_id = self.confluence.get_page_id(self.config["space"], page_name)
        except atlassian.errors.ApiPermissionError as api_error:
            self.logger.error("  User {} doesn't have permissions to access page {} in space {}".format(self.config["username"],
                                                                                                      page_name,
                                                                                                      self.config["space"]))

            raise api_error
            page_id = None
        else:
            self.logger.debug(f"  Found page ID for Page '{page_name}' : {page_id}")

        return page_id


    def add_page(self, page_name, parent_page_id, page_content_in_storage_format):
        self.logger.debug(f"  Adding Page '{page_name}' to parent with ID: {parent_page_id}")

        if not self.dryrun:
            # Try/Except in Future
            #self.logger.debug(f"  DATA: {page_content_in_storage_format}")
            self.confluence.create_page(self.config["space"],
                                        page_name,
                                        page_content_in_storage_format,
                                        parent_id=parent_page_id,
                                        type='page',
                                        representation='storage',
                                        editor='v2')
        else:
            self.logger.info(f"  Refrained from creating Page {page_name}: dryrun mode")


    def update_page(self, page_name, page_content_in_storage_format):

        self.logger.info(f"  *UPDATE* '{page_name}'")
        page_id = self.find_page_id(page_name)

        if not self.dryrun:
            self.confluence.update_page(page_id,
                                    page_name,
                                    page_content_in_storage_format,
                                    type='page',
                                    representation='storage',
                                    minor_edit=False)
        else:
            self.logger.info(f"Refrained from updating Page {page_name}, dryrun")

    def find_page_version(self, page_name):

        self.logger.debug(f"  Call Confluence to get version for page '{page_name}'")

        page_id = self.find_page_id(page_name)

        page_history = self.confluence.history(page_id)[0]["version"]["number"]

        self.logger.debug(f"  Confluence returned version {page_history} for page '{page_name}'")

        return page_history

    def find_parent_name_of_page(self, name):

        self.logger.debug(f"  Call Confluence to get parent for page '{name}'")

        idp = self.find_page_id(name)

        parent = self.confluence.get_page_ancestors(idp)[-1]["title"]

        self.logger.debug(f"  Confluence returned parent of page '{name}' is '{parent}'")

        return parent

    def wait_until(self, condition, interval=0.1, timeout=1):
        start = time.time()
        while not condition and time.time() - start < timeout:
            time.sleep(interval)
