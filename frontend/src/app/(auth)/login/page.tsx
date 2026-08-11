"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { useAuth } from "@/features/auth/auth-provider";
import { AuthFormShell } from "@/features/auth/components/auth-form-shell";
import { FormError } from "@/features/auth/components/form-error";
import { loginSchema, type LoginValues } from "@/features/auth/schemas";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { login } = useAuth();
  const [error, setError] = useState<unknown>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });

  const revoked = params.get("reason") === "session_revoked";

  const onSubmit = handleSubmit(async (values) => {
    setError(null);
    try {
      await login(values);
      router.push("/dashboard");
    } catch (e) {
      setError(e);
    }
  });

  return (
    <AuthFormShell
      title="Welcome back"
      subtitle="Sign in to pick up where you left off."
      footer={
        <>
          New here?{" "}
          <Link href="/register" className="underline underline-offset-4">
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {revoked && (
          <div
            role="status"
            className="rounded-control border border-warning/40 bg-warning/5 p-3 type-body"
          >
            Your session was ended for security reasons. Please sign in again.
          </div>
        )}

        <FormError error={error} />

        <Field
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          error={errors.email?.message}
          {...register("email")}
        />
        <Field
          label="Password"
          type="password"
          autoComplete="current-password"
          error={errors.password?.message}
          {...register("password")}
        />

        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthFormShell>
  );
}

export default function LoginPage() {
  // useSearchParams needs a Suspense boundary to keep the route statically
  // renderable in the App Router.
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
