import Purchases, {
  type CustomerInfo,
  type PurchasesOffering,
  type PurchasesPackage,
} from 'react-native-purchases';

export type PurchaseResult = { ok: boolean; userCancelled?: boolean; message?: string };

function hasPremium(info: CustomerInfo): boolean {
  return typeof info.entitlements.active[ENTITLEMENT_ID] !== 'undefined';
}

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
    return hasPremium(await Purchases.getCustomerInfo());
  } catch {
    return true;
  }
}

// Fires whenever RevenueCat reports the premium entitlement is active. Covers the
// sandbox / StoreKit case where the receipt validates a moment AFTER
// purchasePackage() resolves — which would otherwise strand the user on the
// paywall even though they were charged. Returns an unsubscribe function.
export function addEntitlementListener(onActive: () => void): () => void {
  if (!PURCHASES_ENABLED) return () => {};
  const listener = (info: CustomerInfo) => {
    if (hasPremium(info)) onActive();
  };
  Purchases.addCustomerInfoUpdateListener(listener);
  return () => Purchases.removeCustomerInfoUpdateListener(listener);
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

export async function purchasePackage(pkg: PurchasesPackage): Promise<PurchaseResult> {
  try {
    const { customerInfo } = await Purchases.purchasePackage(pkg);
    if (hasPremium(customerInfo)) return { ok: true };
    // StoreKit completed but RevenueCat hasn't granted the entitlement yet —
    // surface it (and the listener / Restore can still recover) instead of
    // silently looping the paywall.
    return {
      ok: false,
      message: "Payment went through, but premium hasn't activated yet. Give it a moment, or tap Restore purchases.",
    };
  } catch (err: any) {
    if (err?.userCancelled) return { ok: false, userCancelled: true };
    return { ok: false, message: err?.message ?? 'Purchase failed. Please try again.' };
  }
}

export async function restorePurchases(): Promise<boolean> {
  if (!PURCHASES_ENABLED) return false;
  try {
    return hasPremium(await Purchases.restorePurchases());
  } catch {
    return false;
  }
}
