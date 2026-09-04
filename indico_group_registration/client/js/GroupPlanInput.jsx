// The participant-facing plan picker.
//
// It writes a single object into the registration form:
//   {mode: 'none' | 'create' | 'join', plan, name, code, accepted}
// The backend validates the same shape again -- this component exists to make
// the choice, and its consequences, legible before anyone submits.

import checkCodeURL from 'indico-url:plugin_group_registration.check_code';

import _ from 'lodash';
import PropTypes from 'prop-types';
import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {useField} from 'react-final-form';
import {Checkbox, Form, Input, Loader, Message, Radio} from 'semantic-ui-react';

import {Param, Translate} from 'indico/react/i18n';
import {indicoAxios} from 'indico/utils/axios';

import './GroupPlanInput.module.scss';

const MODE_NONE = 'none';
const MODE_CREATE = 'create';
const MODE_JOIN = 'join';

const EMPTY = {mode: MODE_NONE, plan: null, name: '', code: '', accepted: false};

function formatMoney(amount, currency) {
  try {
    return new Intl.NumberFormat(document.documentElement.lang || 'en', {
      style: 'currency',
      currency,
    }).format(amount);
  } catch {
    return `${amount} ${currency}`;
  }
}

/**
 * What one member pays under a plan.
 *
 * Two fees, because they can differ: `basePrice` is the form's standard
 * registration fee, and `payerBasePrice` is what this particular person pays
 * before a group plan is applied -- less than the fee when another plugin has
 * already taken something off, which is the STSA member discount's case.  Which
 * of the two the plan's own rate is worked out from is the organizer's
 * `applies_to` setting, read exactly as `pricing.compute_discount` reads it on
 * the server: against the fee it is the standard fee, so two discounts do not
 * compound; against the total it is what the other discount left behind.
 *
 * Getting that right here is the difference between a quote and a guess -- the
 * price a group plan shows is the one somebody decides to register on.
 */
function planPrice(plan, basePrice, payerBasePrice, appliesTo) {
  if (!plan || !plan.type || !plan.value) {
    return payerBasePrice;
  }
  const rateBase = appliesTo === 'total' ? payerBasePrice : basePrice;
  const discount = plan.type === 'percent' ? (rateBase * plan.value) / 100 : plan.value;
  // Clamped to what the rate applies to, like `plans.discount_for`, and then to
  // zero, like Indico's own total.
  return Math.max(payerBasePrice - Math.min(discount, rateBase), 0);
}

/** The code as it is shown: two halves, because people retype it. */
function normalizeCode(value) {
  return (value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

export default function GroupPlanInput({
  htmlName,
  disabled,
  eventId,
  regformId,
  plans,
  currency,
  basePrice,
  payerBasePrice,
  appliesTo,
  disclaimer,
  allowEarlyPayment,
  enabled,
}) {
  const {input} = useField(htmlName, {allowNull: true});
  const value = input.value || EMPTY;
  const groupPlans = useMemo(() => plans.filter(p => p.size > 1), [plans]);
  // A plugin that discounts the registration overwrites `payerBasePrice`; a
  // server that does not send it at all means nobody has, and the standard fee
  // is what this person pays.
  const payerBase = payerBasePrice ?? basePrice;
  const memberPrice = plan => planPrice(plan, basePrice, payerBase, appliesTo);

  const [lookup, setLookup] = useState({state: 'idle'});
  const lookupSeq = useRef(0);

  const update = useCallback(
    patch => {
      input.onChange({...EMPTY, ...value, ...patch});
    },
    [input, value]
  );

  // -- code lookup ---------------------------------------------------------

  const runLookup = useMemo(
    () =>
      _.debounce(async code => {
        const seq = ++lookupSeq.current;
        if (code.length < 4) {
          setLookup({state: 'idle'});
          return;
        }
        setLookup({state: 'loading'});
        let response;
        try {
          response = await indicoAxios.get(checkCodeURL({event_id: eventId, reg_form_id: regformId}), {
            params: {code},
          });
        } catch (error) {
          if (seq !== lookupSeq.current) {
            return;
          }
          // The lookup is login-only, so that the endpoint cannot be used to
          // guess codes. Somebody registering without an account still gets to
          // join -- the code is checked again when they submit -- so this is a
          // note about the preview, not an error about the code.
          const status = error.response?.status;
          setLookup(
            status === 401 || status === 403
              ? {state: 'unchecked'}
              : {state: 'error', error: Translate.string('Could not check that code.')}
          );
          return;
        }
        if (seq !== lookupSeq.current) {
          // A newer keystroke already won.
          return;
        }
        setLookup(
          response.data.valid
            ? {state: 'found', group: response.data}
            : {state: 'error', error: response.data.error}
        );
      }, 400),
    [eventId, regformId]
  );

  useEffect(() => {
    if (value.mode === MODE_JOIN) {
      runLookup(normalizeCode(value.code));
    } else {
      setLookup({state: 'idle'});
    }
    return () => runLookup.cancel();
  }, [value.mode, value.code, runLookup]);

  // A form with a single group plan leaves nothing to choose, so choosing to
  // create a group is itself the choice of plan. The guard is what stops this
  // from fighting a participant who picked one of several plans.
  useEffect(() => {
    if (value.mode === MODE_CREATE && !value.plan && groupPlans.length === 1) {
      update({plan: groupPlans[0].id});
    }
  }, [value.mode, value.plan, groupPlans, update]);

  // A join link drops the code in the query string; pick it up once.
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current || disabled) {
      return;
    }
    prefilled.current = true;
    const code = new URLSearchParams(window.location.search).get('group_code');
    if (code) {
      input.onChange({...EMPTY, mode: MODE_JOIN, code: normalizeCode(code)});
    }
    // Only ever runs once, deliberately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disabled]);

  if (!enabled || !groupPlans.length) {
    return null;
  }

  const chosenPlan = groupPlans.find(p => p.id === value.plan) || null;
  const needsAcceptance = value.mode === MODE_CREATE || value.mode === MODE_JOIN;

  return (
    <div styleName="group-plan" data-mode={value.mode}>
      {/*
        A plain stack rather than `Form.Group grouped`: Indico's own form CSS
        flattens Semantic's grouped fields onto one line, which runs the three
        choices together with no space between them.
      */}
      <div styleName="modes">
        <Radio
          label={Translate.string('Register individually')}
          checked={value.mode === MODE_NONE}
          disabled={disabled}
          onChange={() => input.onChange({...EMPTY})}
        />
        <Radio
          label={Translate.string('Create a group and invite people')}
          checked={value.mode === MODE_CREATE}
          disabled={disabled}
          onChange={() => update({mode: MODE_CREATE, code: '', accepted: false})}
        />
        <Radio
          label={Translate.string('Join a group with a code')}
          checked={value.mode === MODE_JOIN}
          disabled={disabled}
          onChange={() => update({mode: MODE_JOIN, plan: null, name: '', accepted: false})}
        />
      </div>

      {value.mode === MODE_CREATE && (
        <div styleName="panel">
          <div styleName="plans">
            {groupPlans.map(plan => (
              <label key={plan.id} styleName="plan" data-selected={plan.id === value.plan}>
                <Radio
                  checked={plan.id === value.plan}
                  disabled={disabled}
                  onChange={() => update({plan: plan.id, accepted: false})}
                />
                <span styleName="plan-label">{plan.label}</span>
                <span styleName="plan-seats">
                  <Translate>
                    <Param name="size" value={plan.size} /> members
                  </Translate>
                </span>
                <span styleName="plan-price">
                  {formatMoney(memberPrice(plan), currency)}
                  <small>
                    <Translate>each</Translate>
                  </small>
                </span>
              </label>
            ))}
          </div>

          <Form.Field required>
            <label htmlFor="group-name-input">
              <Translate>Group name</Translate>
            </label>
            <Input
              id="group-name-input"
              value={value.name}
              disabled={disabled}
              maxLength={80}
              placeholder={Translate.string('e.g. your department or company')}
              onChange={(evt, {value: name}) => update({name})}
            />
          </Form.Field>

          {chosenPlan && (
            <Message info>
              <Translate>
                You will get a code and a link to share. Your group confirms itself as soon as{' '}
                <Param name="size" value={chosenPlan.size} /> people have joined, and the rate is
                then final.
              </Translate>
            </Message>
          )}
        </div>
      )}

      {value.mode === MODE_JOIN && (
        <div styleName="panel">
          <Form.Field required>
            <label htmlFor="group-code-input">
              <Translate>Group code</Translate>
            </label>
            <Input
              id="group-code-input"
              value={value.code}
              disabled={disabled}
              maxLength={9}
              placeholder="ABCD-2345"
              onChange={(evt, {value: code}) => update({code: normalizeCode(code), accepted: false})}
              icon={lookup.state === 'loading' ? <Loader active inline size="tiny" /> : undefined}
            />
          </Form.Field>

          {lookup.state === 'error' && <Message negative>{lookup.error}</Message>}
          {lookup.state === 'unchecked' && (
            <Message info>
              <Translate>
                Log in to see the group before you join. You can register without an account -- the code
                is checked either way when you submit.
              </Translate>
            </Message>
          )}
          {lookup.state === 'found' && (
            <Message positive>
              <Message.Header>{lookup.group.name}</Message.Header>
              <p>
                <Translate>
                  <Param name="members" value={lookup.group.members} /> of{' '}
                  <Param name="target" value={lookup.group.target} /> members ·{' '}
                  <Param
                    name="price"
                    value={formatMoney(memberPrice(lookup.group.plan), currency)}
                  />{' '}
                  for you
                </Translate>
              </p>
            </Message>
          )}
        </div>
      )}

      {needsAcceptance && (
        <div styleName="disclaimer">
          {disclaimer && <p>{disclaimer}</p>}
          {!allowEarlyPayment && (
            <p>
              <Translate>
                You will not be able to pay until your group is complete.
              </Translate>
            </p>
          )}
          <Checkbox
            label={Translate.string('I understand and accept these conditions')}
            checked={!!value.accepted}
            disabled={disabled}
            onChange={(evt, {checked}) => update({accepted: checked})}
          />
        </div>
      )}
    </div>
  );
}

GroupPlanInput.propTypes = {
  htmlName: PropTypes.string.isRequired,
  disabled: PropTypes.bool,
  eventId: PropTypes.number.isRequired,
  regformId: PropTypes.number.isRequired,
  plans: PropTypes.array,
  currency: PropTypes.string.isRequired,
  basePrice: PropTypes.number,
  payerBasePrice: PropTypes.number,
  appliesTo: PropTypes.oneOf(['base', 'total']),
  disclaimer: PropTypes.string,
  allowEarlyPayment: PropTypes.bool,
  enabled: PropTypes.bool,
};

GroupPlanInput.defaultProps = {
  disabled: false,
  plans: [],
  basePrice: 0,
  payerBasePrice: null,
  appliesTo: 'base',
  disclaimer: '',
  allowEarlyPayment: true,
  enabled: false,
};
