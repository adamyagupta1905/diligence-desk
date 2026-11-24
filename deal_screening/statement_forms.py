"""Explicit opening statement mapping and financing inputs; no inferred plugs."""
from .three_statements import OPENING, FINANCING


def statement_inputs(st, state, config, forecasts, asset_config):
    st.write('Linked statements: opening balance sheet and financing')
    st.caption('Every opening balance must be supplied, including explicit zeros. Amounts use full currency units. Check source classifications and currency. Other assets/liabilities are held constant; other operating balances enter NWC. Opening equity is total book equity, not DCF equity value.')
    previous = config.get('three_statements', {})
    ledger = {r['metric']:r for r in state.get('ledger_confirmed',[]) if r['fiscal_year']==state.get('ledger_selected_year')}
    mapping={'cash':'cash','receivables':'receivables','inventory':'inventory','other_operating_assets':'other_current_assets',
             'other_assets':'other_noncurrent_assets','payables':'payables','other_operating_liabilities':'other_current_liabilities',
             'other_liabilities':'other_noncurrent_liabilities','equity':'total_equity'}
    rows=[]
    for key in OPENING:
        source=ledger.get(mapping.get(key,''))
        suggested=float(source['normalized_value']) if source else None
        refs=source['record_id'] if source else ''
        if key=='ppe': suggested=asset_config['opening_ppe'];refs='Reviewed fixed-asset input'
        if key=='debt' and all(k in ledger for k in ('short_term_debt','long_term_debt')):
            suggested=sum(float(ledger[k]['normalized_value']) for k in ('short_term_debt','long_term_debt'))
            refs=', '.join(ledger[k]['record_id'] for k in ('short_term_debt','long_term_debt'))
        rows.append({'balance':key,'value':previous.get('opening',{}).get(key,suggested),'reference_for_review':refs})
    st.caption('Ledger suggestions are starting mappings, not verified forecasts: include any securities/other omitted assets in other_assets, exclude financial items from operating NWC, and reconcile any PP&E exclusions. The app never inserts an equity or debt plug.')
    edited=st.data_editor(rows,disabled=['balance','reference_for_review'],hide_index=True,num_rows='fixed',width='stretch',
                          key='opening_statements_'+state.get('revision_id','standalone'))
    opening={r['balance']:r['value'] for r in edited}
    if all(isinstance(v,(int,float)) for v in opening.values()):
        assets=sum(opening[k] for k in ('cash','receivables','inventory','other_operating_assets','ppe','other_assets'))
        liabilities=sum(opening[k] for k in ('payables','other_operating_liabilities','debt','other_liabilities'))
        nwc=opening['receivables']+opening['inventory']+opening['other_operating_assets']-opening['payables']-opening['other_operating_liabilities']
        st.write({'opening_assets':assets,'opening_liabilities_plus_equity':liabilities+opening['equity'],
                  'opening_balance_difference':assets-liabilities-opening['equity'],'implied_opening_nwc':nwc,
                  'opening_net_debt':opening['debt']-opening['cash']})
    else:
        st.info('Complete all opening balances; unknown balances cannot silently become zero.')
    st.write('Annual financing — interest uses OPENING debt; all cash movements are explicit')
    prior=previous.get('financing',[]);finance=[]
    for i in range(len(forecasts)):
        r=prior[i] if i<len(prior) else {}
        finance.append({'year':i+1,'interest_pct':r.get('interest_rate',.05)*100,
                        **{k:r.get(k,0.) for k in FINANCING if k!='interest_rate'}})
    edited_finance=st.data_editor(finance,disabled=['year'],hide_index=True,num_rows='fixed',width='stretch',
                                 key='statement_financing_'+str(len(forecasts))+'_'+state.get('revision_id','standalone'))
    st.caption('Initial 5% interest and zero financing/dividends are illustrative assumptions. New debt has no interest until next year; repayments do not reduce current-year interest. Interest paid is classified in CFO. Cash taxes equal P&L tax expense; no deferred tax, interest income, covenants, revolver or automatic funding.')
    st.form_submit_button('Preview opening statement balances')
    finance=[]
    for r in edited_finance:
        rate=r['interest_pct']
        finance.append({'year':r['year'],'interest_rate':rate/100 if isinstance(rate,(int,float)) else None,
                        **{k:r[k] for k in FINANCING if k!='interest_rate'}})
    return {'opening':opening,'financing':finance}
