import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Linking, Pressable, ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { PurchasesPackage } from 'react-native-purchases';

import { track } from '../lib/analytics';
import {
  addEntitlementListener,
  getOffering,
  getTrialEligibility,
  purchasePackage,
  restorePurchases,
} from '../lib/purchases';
import { fonts } from '../lib/theme';

type Eligibility = Record<string, boolean>;

// Human-readable intro period straight from StoreKit, e.g. P1W → "1 week".
function introDurationLabel(unit: string, count: number): string {
  const word =
    ({ DAY: 'day', WEEK: 'week', MONTH: 'month', YEAR: 'year' } as Record<string, string>)[unit] ??
    unit.toLowerCase();
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

// The package's intro offer IF this account can still claim it. Returns null
// when there is no offer or the trial was already used — so the UI never
// promises something Apple won't honor.
function eligibleIntro(pkg: PurchasesPackage, eligible: Eligibility) {
  const intro = pkg.product.introPrice;
  if (!intro) return null;
  if (eligible[pkg.product.identifier] === false) return null;
  return intro;
}

// Short "N days/weeks free" badge for a card — only for a genuine free trial.
function freeTrialLabel(pkg: PurchasesPackage, eligible: Eligibility): string | null {
  const intro = eligibleIntro(pkg, eligible);
  if (!intro || intro.price !== 0) return null;
  return `${introDurationLabel(intro.periodUnit, intro.periodNumberOfUnits)} free`;
}

// Full price/offer line for the selected plan, e.g.
// "1 week free, then $39.99 / year" or, with no trial, "$4.99 / month".
function offerSummary(pkg: PurchasesPackage, eligible: Eligibility): string {
  const base = `${pkg.product.priceString} / ${isAnnual(pkg) ? 'year' : 'month'}`;
  const intro = eligibleIntro(pkg, eligible);
  if (!intro) return base;
  const dur = introDurationLabel(intro.periodUnit, intro.periodNumberOfUnits);
  return intro.price === 0 ? `${dur} free, then ${base}` : `${intro.priceString} for ${dur}, then ${base}`;
}

const BENEFITS = [
  'Unlimited conversations with JAI',
  'Your morning reflection, every day',
  'Your full history and dashboard',
  'A companion that remembers your week',
];

// Apple requires Terms (EULA) + Privacy links on an auto-renewable paywall.
const TERMS_URL = 'https://ai-journalling-app-frontend.vercel.app/terms';
const PRIVACY_URL = 'https://ai-journalling-app-frontend.vercel.app/privacy';

function isAnnual(pkg: PurchasesPackage): boolean {
  return pkg.packageType === 'ANNUAL';
}

function perMonthString(pkg: PurchasesPackage): string | null {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: pkg.product.currencyCode,
    }).format(pkg.product.price / 12);
  } catch {
    return null;
  }
}

function savingsPct(monthly?: PurchasesPackage, annual?: PurchasesPackage): number | null {
  if (!monthly || !annual || !monthly.product.price) return null;
  const pct = Math.round((1 - annual.product.price / 12 / monthly.product.price) * 100);
  return pct > 0 ? pct : null;
}

function PlanCard({
  pkg,
  selected,
  onPress,
  badge,
  trialLabel,
}: {
  pkg: PurchasesPackage;
  selected: boolean;
  onPress: () => void;
  badge?: string;
  trialLabel?: string | null;
}) {
  const annual = isAnnual(pkg);
  const period = annual ? 'year' : 'month';
  const perMonth = annual ? perMonthString(pkg) : null;
  return (
    <Pressable
      onPress={onPress}
      style={{
        borderWidth: 1.5,
        borderColor: selected ? '#2A2825' : '#DDD8D0',
        backgroundColor: selected ? 'rgba(42,40,37,0.04)' : 'transparent',
        borderRadius: 16,
        padding: 16,
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ fontFamily: fonts.sansMedium, fontSize: 16, color: '#2A2825' }}>
          {annual ? 'Yearly' : 'Monthly'}
        </Text>
        {badge ? (
          <View style={{ backgroundColor: '#6E9B7A', borderRadius: 999, paddingHorizontal: 9, paddingVertical: 3 }}>
            <Text style={{ fontFamily: fonts.sansMedium, fontSize: 11, color: '#fff' }}>{badge}</Text>
          </View>
        ) : null}
      </View>
      <Text style={{ fontFamily: fonts.sans, fontSize: 14, color: '#6E6B64', marginTop: 6 }}>
        {pkg.product.priceString} / {period}
        {perMonth ? `   ·   ${perMonth} / mo` : ''}
      </Text>
      {trialLabel ? (
        <Text style={{ fontFamily: fonts.sansMedium, fontSize: 13, color: '#6E9B7A', marginTop: 4 }}>
          {trialLabel}
        </Text>
      ) : null}
    </Pressable>
  );
}

export function Paywall({ onPurchased }: { onPurchased: () => void }) {
  const [packages, setPackages] = useState<PurchasesPackage[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [eligible, setEligible] = useState<Eligibility>({});
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  // Auto-dismiss the moment RevenueCat confirms premium — even if it lands
  // slightly after purchasePackage() resolves (the sandbox/StoreKit delay that
  // was stranding users on the paywall after a successful charge).
  const onPurchasedRef = useRef(onPurchased);
  onPurchasedRef.current = onPurchased;
  useEffect(() => addEntitlementListener(() => onPurchasedRef.current()), []);

  useEffect(() => {
    getOffering().then((o) => {
      const pkgs = o?.availablePackages ?? [];
      setPackages(pkgs);
      const annual = pkgs.find(isAnnual);
      setSelectedId((annual ?? pkgs[0])?.identifier ?? null);
      setLoaded(true);
      track('paywall_viewed', { has_packages: pkgs.length > 0, package_count: pkgs.length });
      const ids = pkgs.map((p) => p.product.identifier);
      if (ids.length) getTrialEligibility(ids).then(setEligible);
    });
  }, []);

  const selectPlan = (pkg: PurchasesPackage) => {
    setSelectedId(pkg.identifier);
    track('paywall_plan_selected', { plan: pkg.packageType, identifier: pkg.identifier });
  };

  const monthly = packages.find((p) => p.packageType === 'MONTHLY');
  const annual = packages.find(isAnnual);
  const selected = packages.find((p) => p.identifier === selectedId) ?? null;
  const save = savingsPct(monthly, annual);
  const selectedIntro = selected ? eligibleIntro(selected, eligible) : null;
  const ctaLabel = selectedIntro && selectedIntro.price === 0 ? 'Start free trial' : 'Subscribe';

  const subscribe = async () => {
    if (!selected) return;
    const ctx = {
      plan: selected.packageType,
      identifier: selected.identifier,
      is_trial: Boolean(selectedIntro && selectedIntro.price === 0),
      cta: ctaLabel,
    };
    track('paywall_subscribe_clicked', ctx);
    setBusy(true);
    const res = await purchasePackage(selected);
    setBusy(false);
    if (res.ok) {
      track('paywall_purchase_succeeded', ctx);
      onPurchased();
    } else if (res.userCancelled) {
      track('paywall_purchase_cancelled', ctx);
    } else {
      track('paywall_purchase_failed', { ...ctx, message: res.message ?? '' });
      if (res.message) Alert.alert('Just a moment', res.message);
    }
  };

  const restore = async () => {
    track('paywall_restore_clicked');
    setBusy(true);
    const ok = await restorePurchases();
    setBusy(false);
    track('paywall_restore_result', { restored: ok });
    if (ok) onPurchased();
  };

  const priceSummary = selected ? offerSummary(selected, eligible) : 'Choose your plan';

  return (
    <SafeAreaView className="flex-1 bg-paper">
      <ScrollView contentContainerStyle={{ flexGrow: 1, paddingHorizontal: 28, paddingTop: 24, paddingBottom: 28 }}>
        <View style={{ flex: 1, justifyContent: 'center' }}>
          <Text style={{ fontFamily: fonts.serifMedium, fontSize: 30, lineHeight: 38, color: '#2A2825' }}>
            JAI Premium
          </Text>
          <Text style={{ fontFamily: fonts.serif, fontSize: 18, lineHeight: 28, color: '#6E6B64', marginTop: 12 }}>
            Keep your companion close — every day.
          </Text>

          <View style={{ marginTop: 24, gap: 14 }}>
            {BENEFITS.map((b) => (
              <View key={b} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10 }}>
                <Text style={{ fontFamily: fonts.sans, fontSize: 16, color: '#6E9B7A', marginTop: 1 }}>✓</Text>
                <Text style={{ flex: 1, fontFamily: fonts.sans, fontSize: 16, lineHeight: 23, color: '#56534B' }}>
                  {b}
                </Text>
              </View>
            ))}
          </View>

          {packages.length > 0 ? (
            <View style={{ marginTop: 26, gap: 12 }}>
              {annual ? (
                <PlanCard
                  pkg={annual}
                  selected={selectedId === annual.identifier}
                  onPress={() => selectPlan(annual)}
                  badge={save ? `Save ${save}%` : 'Best value'}
                  trialLabel={freeTrialLabel(annual, eligible)}
                />
              ) : null}
              {monthly ? (
                <PlanCard
                  pkg={monthly}
                  selected={selectedId === monthly.identifier}
                  onPress={() => selectPlan(monthly)}
                  trialLabel={freeTrialLabel(monthly, eligible)}
                />
              ) : null}
            </View>
          ) : null}
        </View>

        <View>
          <Text
            style={{
              fontFamily: fonts.sansMedium,
              fontSize: 15,
              color: '#38342F',
              textAlign: 'center',
              marginBottom: 14,
            }}
          >
            {priceSummary}
          </Text>

          <Pressable
            onPress={subscribe}
            disabled={busy || !selected}
            className="items-center justify-center rounded-2xl bg-ink active:opacity-80"
            style={{ height: 52, opacity: selected ? 1 : 0.4 }}
          >
            {busy ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={{ fontFamily: fonts.sansMedium, fontSize: 16, color: '#fff' }}>{ctaLabel}</Text>
            )}
          </Pressable>

          <Pressable onPress={restore} hitSlop={8} className="mt-4 items-center">
            <Text style={{ fontFamily: fonts.sans, fontSize: 13, color: '#9A9790' }}>Restore purchases</Text>
          </Pressable>

          {loaded && packages.length === 0 ? (
            <Pressable
              onPress={() => {
                track('paywall_dismissed', { reason: 'no_packages' });
                onPurchased();
              }}
              hitSlop={8}
              className="mt-3 items-center"
            >
              <Text style={{ fontFamily: fonts.sans, fontSize: 13, color: '#B7B4AD' }}>Not now</Text>
            </Pressable>
          ) : null}

          <View className="mt-5 flex-row justify-center" style={{ gap: 18 }}>
            <Pressable onPress={() => Linking.openURL(TERMS_URL)}>
              <Text style={{ fontFamily: fonts.sans, fontSize: 11, color: '#B7B4AD' }}>Terms</Text>
            </Pressable>
            <Pressable onPress={() => Linking.openURL(PRIVACY_URL)}>
              <Text style={{ fontFamily: fonts.sans, fontSize: 11, color: '#B7B4AD' }}>Privacy</Text>
            </Pressable>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}
