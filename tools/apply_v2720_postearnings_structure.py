from pathlib import Path
p=Path('app.py'); s=p.read_text(); orig=s
s=s.replace('APP_VERSION = "27.19"','APP_VERSION = "27.20"')
old='''                base=float(before.iloc[-1]); last=float(after.iloc[-1]); day1=float(after.iloc[0])
                return {"current_move_pct":round((last/base-1)*100,2),
                        "day1_move_pct":round((day1/base-1)*100,2),
                        "sessions_since":int(len(after)),
                        "first_post_session":pd.Timestamp(after.index[0]).strftime("%Y-%m-%d"),
                        "after_hours_aligned":bool(after_close)}'''
new='''                base=float(before.iloc[-1]); last=float(after.iloc[-1]); day1=float(after.iloc[0])
                # Daily closes cannot see the exact pre/post-market print, but the
                # first regular-session close captures the overnight repricing.
                # Measure what happened AFTER that reaction separately so a huge
                # gap followed by a tight base is not mislabeled as a spent move.
                post=after.iloc[1:] if len(after)>1 else after.iloc[0:0]
                post_high=float(post.max()) if len(post) else day1
                post_low=float(post.min()) if len(post) else day1
                day1_move=(day1/base-1)*100
                current_move=(last/base-1)*100
                retention=(current_move/day1_move*100) if abs(day1_move)>=0.75 else None
                post_range_pct=((post_high-post_low)/abs(day1)*100) if day1 and len(post) else 0.0
                drift_from_day1=((last/day1)-1)*100 if day1 else 0.0
                return {"current_move_pct":round(current_move,2),
                        "day1_move_pct":round(day1_move,2),
                        "sessions_since":int(len(after)),
                        "first_post_session":pd.Timestamp(after.index[0]).strftime("%Y-%m-%d"),
                        "after_hours_aligned":bool(after_close),
                        "reaction_retained_pct":round(retention,1) if retention is not None else None,
                        "post_reaction_range_pct":round(post_range_pct,2),
                        "drift_from_day1_pct":round(drift_from_day1,2)}'''
assert old in s; s=s.replace(old,new)
old='''            if move_consumed_pct<35: setup_stage="FRESH"
            elif move_consumed_pct<65: setup_stage="DEVELOPING"
            elif move_consumed_pct<90: setup_stage="MATURE"
            else: setup_stage="EXTENDED"

            expected_window=14 if profile.get("has_exc14_data") else 10'''
new='''            if move_consumed_pct<35: setup_stage="FRESH"
            elif move_consumed_pct<65: setup_stage="DEVELOPING"
            elif move_consumed_pct<90: setup_stage="MATURE"
            else: setup_stage="EXTENDED"

            # Post-reaction structure is a second dimension from historical
            # magnitude consumption. A >100% historical move can remain actionable
            # if the overnight/Day-1 repricing is retained and price compresses.
            reaction_retained=_safe_float(cur.get("reaction_retained_pct"))
            post_range=_safe_float(cur.get("post_reaction_range_pct"))
            drift_day1=_safe_float(cur.get("drift_from_day1_pct"))
            sessions_now=int(cur.get("sessions_since") or 0)
            large_reaction=abs(reaction_move)>=max(5.0,expected*0.45)
            held_reaction=(reaction_retained is not None and reaction_retained>=65 and (move*reaction_move)>0)
            compressed=bool(sessions_now>=3 and post_range is not None and post_range<=max(8.0,abs(reaction_move)*0.35))
            post_structure="ACTIVE REACTION"
            structure_bonus=0.0
            second_leg=False
            if setup_type if False else False: pass
            # setup_type is assigned below; classify direction-agnostically here.
            if large_reaction and held_reaction and compressed:
                post_structure="POST-EARNINGS BASE"; structure_bonus=10.0
            elif large_reaction and held_reaction:
                post_structure="HOLDING REACTION"; structure_bonus=5.0
            elif reaction_retained is not None and reaction_retained<=25:
                post_structure="REACTION FADING"

            expected_window=14 if profile.get("has_exc14_data") else 10'''
assert old in s; s=s.replace(old,new)
# remove syntactically valid but ugly placeholder line
s=s.replace('            if setup_type if False else False: pass\n','')
old='''            if setup_type=="REVERSION":
                trade_direction="bearish" if reaction_sign>0 else "bullish"
                directional_rotation=(f_out or t_out) if trade_direction=="bearish" else (f_in or t_in)
                reversion_confirmed=bool((recovery_pct or 0)>=25 and directional_rotation)
            else:
                trade_direction="bullish" if reaction_sign>0 else "bearish"
                directional_rotation=(f_in or t_in) if trade_direction=="bullish" else (f_out or t_out)
                reversion_confirmed=False
'''
new='''            if setup_type=="REVERSION":
                trade_direction="bearish" if reaction_sign>0 else "bullish"
                directional_rotation=(f_out or t_out) if trade_direction=="bearish" else (f_in or t_in)
                reversion_confirmed=bool((recovery_pct or 0)>=25 and directional_rotation)
                if reversion_confirmed and sessions_now>=3:
                    post_structure="REVERSION BASE"; structure_bonus=max(structure_bonus,8.0)
            else:
                trade_direction="bullish" if reaction_sign>0 else "bearish"
                directional_rotation=(f_in or t_in) if trade_direction=="bullish" else (f_out or t_out)
                reversion_confirmed=False
                if post_structure=="POST-EARNINGS BASE" and directional_rotation:
                    post_structure="SECOND-LEG SETUP"; structure_bonus=14.0; second_leg=True
'''
assert old in s; s=s.replace(old,new)
old='''                elif move_consumed_pct<90: current_score+=4
                else: current_score-=8
                if retained_pct is not None and retained_pct>=50 and not round_trip: current_score+=4'''
new='''                elif move_consumed_pct<90: current_score+=4
                else:
                    # Do not punish a historically oversized move if the initial
                    # earnings repricing has been retained and built a new base.
                    current_score+=(-1 if post_structure in ("HOLDING REACTION","POST-EARNINGS BASE") else 4 if second_leg else -8)
                current_score+=structure_bonus
                if retained_pct is not None and retained_pct>=50 and not round_trip: current_score+=4'''
assert old in s; s=s.replace(old,new)
# window penalty should not punish second-leg/base states
s=s.replace('''            if window_progress_pct is not None and window_progress_pct>=100 and setup_type=="CONTINUATION":
                current_score-=6''','''            if window_progress_pct is not None and window_progress_pct>=100 and setup_type=="CONTINUATION" and post_structure not in ("POST-EARNINGS BASE","SECOND-LEG SETUP"):
                current_score-=6''')
old='''                "retained_pct_of_day1_move":retained_pct,
                "round_trip":round_trip,
            }'''
new='''                "retained_pct_of_day1_move":retained_pct,
                "round_trip":round_trip,
                "post_earnings_structure":post_structure,
                "structure_bonus":round(structure_bonus,1),
                "second_leg":second_leg,
                "reaction_retained_pct":reaction_retained,
                "post_reaction_range_pct":post_range,
                "drift_from_day1_pct":drift_day1,
            }'''
assert old in s; s=s.replace(old,new)
# UI: expose structure and explain that magnitude is not the same as opportunity state.
old='''   const flags=`${x.setup_type==="CONTINUATION"?'<span class="histRunner">CONTINUATION</span>':'<span class="reversionFlag">REVERSION</span>'}${x.reversion_confirmed?'<span class="histRunner">REVERSION CONFIRMED</span>':""}${x.round_trip?'<span class="givebackFlag">ROUND TRIP</span>':""}`;
   const windowNote=`<div class="tiny">${x.setup_stage||"—"} · move consumed ${x.move_consumed_pct==null?"—":Number(x.move_consumed_pct).toFixed(0)+"%"} · runway ${x.remaining_runway_pct==null?"—":Number(x.remaining_runway_pct).toFixed(0)+"%"}${x.remaining_runway_abs_pct==null?"":` (~${Number(x.remaining_runway_abs_pct).toFixed(1)} pts)`}</div><div class="tiny">Drift window: ${x.drift_window_progress_pct==null?"—":x.drift_window_progress_pct+"%"} of ~${x.drift_window_sessions}D${x.setup_type==="REVERSION"&&x.recovery_pct!=null?` · reaction recovery ${Number(x.recovery_pct).toFixed(0)}%`:""}</div>`;'''
new='''   const structure=x.post_earnings_structure||"ACTIVE REACTION";
   const structureBadge=x.second_leg?'<span class="histRunner">SECOND LEG</span>':(structure==="POST-EARNINGS BASE"?'<span class="histRunner">BASE</span>':(structure==="REVERSION BASE"?'<span class="reversionFlag">REVERSION BASE</span>':""));
   const flags=`${x.setup_type==="CONTINUATION"?'<span class="histRunner">CONTINUATION</span>':'<span class="reversionFlag">REVERSION</span>'}${structureBadge}${x.reversion_confirmed?'<span class="histRunner">REVERSION CONFIRMED</span>':""}${x.round_trip?'<span class="givebackFlag">ROUND TRIP</span>':""}`;
   const windowNote=`<div class="tiny"><b>${structure}</b>${x.reaction_retained_pct==null?"":` · reaction retained ${Number(x.reaction_retained_pct).toFixed(0)}%`}${x.post_reaction_range_pct==null?"":` · post-reaction range ${Number(x.post_reaction_range_pct).toFixed(1)}%`}</div><div class="tiny">Historical magnitude: ${x.setup_stage||"—"} · consumed ${x.move_consumed_pct==null?"—":Number(x.move_consumed_pct).toFixed(0)+"%"} · magnitude runway ${x.remaining_runway_pct==null?"—":Number(x.remaining_runway_pct).toFixed(0)+"%"}</div><div class="tiny">Drift window: ${x.drift_window_progress_pct==null?"—":x.drift_window_progress_pct+"%"} of ~${x.drift_window_sessions}D${x.setup_type==="REVERSION"&&x.recovery_pct!=null?` · reaction recovery ${Number(x.recovery_pct).toFixed(0)}%`:""}</div>`;'''
assert old in s; s=s.replace(old,new)
assert s!=orig
p.write_text(s)
print('v27.20 patched')
