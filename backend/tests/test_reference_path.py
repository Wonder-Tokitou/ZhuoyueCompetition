import unittest
from types import SimpleNamespace as NS
from copy import deepcopy
from backend.app.domain.progressive import Configuration, preview, apply_impact
from backend.app.domain.path_comparison import path_comparison

class ReferenceTests(unittest.TestCase):
 def setUp(self):
  self.snapshot={'base_metrics':{'revenue':100,'gross_margin':'20%','market_share':'10%'},'nodes':[{'id':i,'idx':i,'options':[{'key':k,'label':f'策略{i}{k}'} for k in 'ABC']} for i in range(1,4)]}
  self.config={'enabled':True,'cash_flow_amount':-10,'rules':{f'{i}:{k}':dict(revenue_pct=10 if k=='B' else 0,margin_pp=0,expense_pct=0,cash_pct=0,share_pp=0) for i in range(1,4) for k in 'ABC'},'reference_path':{'path':'A→A→A','kind':'recommended'}}
  initial=preview(self.snapshot,self.config,{'operating_cost':80,'operating_expense':10})['baseline']
  self.snapshot.update(base_metrics=initial,simulation=deepcopy(self.config))
 def turns(self,keys):
  state=self.snapshot['base_metrics'];turns=[]
  for i,k in enumerate(keys,1):
   state=apply_impact(state,self.config['rules'][f'{i}:{k}'])
   turns.append(NS(node_id=i,chosen_option=k,after_metrics_json=state))
  return turns
 def test_same_path_and_incomplete(self):
  turns=self.turns('AAA');result=path_comparison(self.snapshot,turns)
  self.assertTrue(all(r['same'] for r in result['rounds']))
  self.assertTrue(all(v==0 for r in result['rounds'] for v in r['difference'].values()))
  self.assertIsNone(path_comparison(self.snapshot,turns[:2]))
 def test_compounding_and_label_identity(self):
  result=path_comparison(self.snapshot,self.turns('BBB'))
  self.assertEqual(result['rounds'][-1]['difference']['revenue'],33.1)
  self.assertEqual(result['rounds'][0]['student_strategy'],'策略1B')
  self.assertEqual(result['rounds'][0]['reference_strategy'],'策略1A')
 def test_optional_and_validation(self):
  self.assertIsNone(path_comparison({},[]))
  for value in ['A→B','D→A→A','A→B→C→A']:
   with self.assertRaises(ValueError):Configuration.model_validate({**self.config,'reference_path':{'path':value}})
  with self.assertRaises(ValueError):Configuration.model_validate({**self.config,'reference_path':{'path':'A→A→A','kind':'invented'}})
  self.assertIsNone(Configuration.model_validate({**self.config,'reference_path':None}).reference_path)
 def test_frozen_configuration(self):
  turns=self.turns('BBB');before=path_comparison(self.snapshot,turns)
  self.config['reference_path']['path']='B→B→B'
  self.assertEqual(path_comparison(self.snapshot,turns),before)

if __name__=='__main__':unittest.main()
