import { useEffect, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { PurchasesPackage } from 'react-native-purchases';

import { getOffering, purchasePackage, restorePurchases } from '../lib/purchases';
import { fonts } from '../lib/theme';

const BENEFITS = [
  'Unlimited conversations with JAI',
  'Your morning reflection, every day',
  'Your full history and dashboard',
  'A companion that remembers your week',
];

// Apple requires Terms (EULA) + Privacy links on an auto-renewable paywall.
const TERMS_URL = 'https://ai-journalling-app-frontend.vercel.app/terms';
const PRIVACY_URL = 'https://ai-journalling-app-frontend.vercel.app/privacy';

function priceLine(pkg: PurchasesPackage | null): string {
  if (!pkg) return '3 days free, then $6.99 / month';
  const price = pkg.product.priceString;
  const intro = pkg.product.introPrice;
  if (intro && intro.periodNumberOfUnits > 0) {
    const unit = intro.periodUnit.toLowerCase();
    const n = intro.periodNumberOfUnits;
    return `${n} ${unit}${n > 1 ? 's' : ''} free, then ${price} / month`;
  }
  return `${price} / month`;
}

export function Paywall({ onPurchased }: { onPurchased: () => void }) {
  const [pkg, setPkg] = useState<PurchasesPackage | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getOffering().then((o) => {
      setPkg(o?.availablePackages?.[0] ?? null);
      setLoaded(true);
    });
  }, []);

  const subscribe = async () => {
    if (!pkg) return;
    setBusy(true);
    const ok = await purchasePackage(pkg);
    setBusy(false);
    if (ok) onPurchased();
  };

  const restore = async () => {
    setBusy(true);
    const ok = await restorePurchases();
    setBusy(false);
    if (ok) onPurchased();
  };

  return (
    <SafeAreaView className="flex-1 bg-paper">
      <ScrollView contentContainerStyle={{ flexGrow: 1, paddingHorizontal: 28, paddingTop: 24, paddingBottom: 28 }}>
        <View className="flex-1 justify-center">
          <Text style={{ fontFamily: fonts.serifMedium, fontSize: 30, lineHeight: 38, color: '#2A2825' }}>
            JAI Premium
          </Text>
          <Text style={{ fontFamily: fonts.serif, fontSize: 18, lineHeight: 28, color: '#6E6B64', marginTop: 12 }}>
            Keep your companion close — every day.
          </Text>

          <View style={{ marginTop: 28, gap: 14 }}>
            {BENEFITS.map((b) => (
              <View key={b} className="flex-row items-start" style={{ gap: 10 }}>
                <Text style={{ fontFamily: fonts.sans, fontSize: 16, color: '#6E9B7A', marginTop: 1 }}>✓</Text>
                <Text style={{ flex: 1, fontFamily: fonts.sans, fontSize: 16, lineHeight: 23, color: '#56534B' }}>
                  {b}
                </Text>
              </View>
            ))}
          </View>
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
            {priceLine(pkg)}
          </Text>

          <Pressable
            onPress={subscribe}
            disabled={busy || !pkg}
            className="items-center justify-center rounded-2xl bg-ink active:opacity-80"
            style={{ height: 52, opacity: pkg ? 1 : 0.4 }}
          >
            {busy ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={{ fontFamily: fonts.sansMedium, fontSize: 16, color: '#fff' }}>Start free trial</Text>
            )}
          </Pressable>

          <Pressable onPress={restore} hitSlop={8} className="mt-4 items-center">
            <Text style={{ fontFamily: fonts.sans, fontSize: 13, color: '#9A9790' }}>Restore purchases</Text>
          </Pressable>

          {loaded && !pkg ? (
            <Pressable onPress={onPurchased} hitSlop={8} className="mt-3 items-center">
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
