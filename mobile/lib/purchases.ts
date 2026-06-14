import Purchases, { type PurchasesOffering, type PurchasesPackage } from 'react-native-purchases';

// RevenueCat is gated behind its API key (like the analytics pattern). When the
// key is unset the whole paywall is a no-op: isEntitled() returns true so nobody
// is gated. Set EXPO_PUBLIC_REVENUECAT_API_KEY + create products to flip it on.
const API_KEY = process.env.EXPO_PUBLIC_REVENUECAT_API_KEY ?? '';
const ENTITLEMENT_ID = 'premium';

export const PURCHASES_ENABLED = Boolean(API_KEY);

let configured = false;

export async function configurePurchases(appUserId: string): Promise<void> {
  if (!PURCHASES_ENABLED || configured) return;
  try {
    Purchases.configure({ apiKey: API_KEY, appUserID: appUserId });
    configured = true;
  } catch {
    // ignore — paywall simply stays inert
  }
}

// True when the user may use the app. Fails OPEN: when billing is disabled or
// the SDK errors, we never trap the user behind a broken paywall.
export async function isEntitled(): Promise<boolean> {
  if (!PURCHASES_ENABLED) return true;
  try {
    const info = await Purchases.getCustomerInfo();
    return typeof info.entitlements.active[ENTITLEMENT_ID] !== 'undefined';
  } catch {
    return true;
  }
}

export async function getOffering(): Promise<PurchasesOffering | null> {
  if (!PURCHASES_ENABLED) return null;
  try {
    const offerings = await Purchases.getOfferings();
    return offerings.current ?? null;
  } catch {
    return null;
  }
}

export async function purchasePackage(pkg: PurchasesPackage): Promise<boolean> {
  try {
    const { customerInfo } = await Purchases.purchasePackage(pkg);
    return typeof customerInfo.entitlements.active[ENTITLEMENT_ID] !== 'undefined';
  } catch {
    // user cancelled or the purchase failed
    return false;
  }
}

export async function restorePurchases(): Promise<boolean> {
  if (!PURCHASES_ENABLED) return false;
  try {
    const info = await Purchases.restorePurchases();
    return typeof info.entitlements.active[ENTITLEMENT_ID] !== 'undefined';
  } catch {
    return false;
  }
}
