"""Every doctype shipped by this app needs a controller class whose name Frappe can
derive from the doctype name: spaces removed, capitals kept ("SplINH Settings" ->
SplINHSettings). A wrong spelling only shows up as "ImportError: <doctype>" when
someone opens the document, so check it offline.

Run:  cd apps/splinh && ../../env/bin/python -m unittest splinh.tests.test_doctype_controllers
"""

import importlib
import json
import os
import unittest

DOCTYPE_ROOT = os.path.join(os.path.dirname(__file__), "..", "splinh", "doctype")


class TestDoctypeControllers(unittest.TestCase):
	def test_every_doctype_has_the_controller_class_frappe_expects(self):
		checked = []
		for folder in sorted(os.listdir(DOCTYPE_ROOT)):
			definition = os.path.join(DOCTYPE_ROOT, folder, f"{folder}.json")
			if not os.path.exists(definition):
				continue
			with open(definition) as f:
				doctype = json.load(f)["name"]
			expected = doctype.replace(" ", "").replace("-", "")
			module = importlib.import_module(f"splinh.splinh.doctype.{folder}.{folder}")
			self.assertTrue(
				hasattr(module, expected),
				f"{doctype}: {folder}.py must define class {expected} (Frappe's naming rule)",
			)
			checked.append(doctype)
		self.assertIn("SplINH Settings", checked)
		self.assertIn("Phone Lookup", checked)


if __name__ == "__main__":
	unittest.main()
