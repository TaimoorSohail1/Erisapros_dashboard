import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  type CognitoUserSession,
  type IAuthenticationDetailsData,
} from "amazon-cognito-identity-js";

const AUTH_ENABLED = import.meta.env.VITE_AUTH_ENABLED === "true";
const USER_POOL_ID = import.meta.env.VITE_COGNITO_USER_POOL_ID || "";
const CLIENT_ID = import.meta.env.VITE_COGNITO_APP_CLIENT_ID || "";

let pendingPasswordUser: CognitoUser | null = null;

function userPool(): CognitoUserPool | null {
  if (!AUTH_ENABLED) return null;
  if (!USER_POOL_ID || !CLIENT_ID) {
    throw new Error("Cognito configuration is incomplete.");
  }
  return new CognitoUserPool({ UserPoolId: USER_POOL_ID, ClientId: CLIENT_ID });
}

export function authenticationEnabled(): boolean {
  return AUTH_ENABLED;
}

export async function getIdToken(): Promise<string | null> {
  const pool = userPool();
  if (!pool) return null;
  const user = pool.getCurrentUser();
  if (!user) return null;

  return new Promise((resolve, reject) => {
    user.getSession((error: Error | null, session: CognitoUserSession | null) => {
      if (error) return reject(error);
      resolve(session?.isValid() ? session.getIdToken().getJwtToken() : null);
    });
  });
}

export async function signIn(
  email: string,
  password: string,
): Promise<"authenticated" | "new-password-required"> {
  const pool = userPool();
  if (!pool) return "authenticated";
  const user = new CognitoUser({ Username: email.trim(), Pool: pool });
  const details: IAuthenticationDetailsData = { Username: email.trim(), Password: password };

  return new Promise((resolve, reject) => {
    user.authenticateUser(new AuthenticationDetails(details), {
      onSuccess: () => resolve("authenticated"),
      onFailure: reject,
      newPasswordRequired: () => {
        pendingPasswordUser = user;
        resolve("new-password-required");
      },
    });
  });
}

export async function completeNewPassword(password: string): Promise<void> {
  if (!pendingPasswordUser) throw new Error("The temporary login session has expired.");
  const user = pendingPasswordUser;
  await new Promise<void>((resolve, reject) => {
    user.completeNewPasswordChallenge(password, {}, {
      onSuccess: () => {
        pendingPasswordUser = null;
        resolve();
      },
      onFailure: reject,
    });
  });
}

export function signOut(): void {
  userPool()?.getCurrentUser()?.signOut();
  pendingPasswordUser = null;
}
