import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('order_patch',ROOT/'scripts/patch-order-recovery.py')
patcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(patcher)


class OrderRecovery(unittest.TestCase):
    def test_unknown_layout_fails_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'app';(source/'ui').mkdir(parents=True)
            (source/'control.py').write_text('unchanged = True\n',encoding='utf-8')
            (source/'ui/index.html').write_text('<p>custom</p>',encoding='utf-8')
            with self.assertRaises(RuntimeError):patcher.patch(source,Path(folder)/'backup',ROOT/'lab-patches')
            self.assertEqual((source/'control.py').read_text(encoding='utf-8'),'unchanged = True\n')
            self.assertFalse((Path(folder)/'backup').exists())

    def test_idempotent_replacement_does_not_duplicate_helper(self):
        new='function databaseRecoveryMessage(){}\nfunction tiles(){}'
        self.assertEqual(patcher.replace(new,'function tiles(){}',new),new)

    def test_ambiguous_layout_is_rejected(self):
        with self.assertRaises(RuntimeError):patcher.replace('old old','old','new')

if __name__=='__main__':unittest.main()
