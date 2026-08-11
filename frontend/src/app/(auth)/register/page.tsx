"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { useAuth } from "@/features/auth/auth-provider";
import { AuthFormShell } from "@/features/auth/components/auth-form-shell";
import { FormError } from "@/features/auth/components/form-error";
import { registerSchema, type RegisterValues } from "@/features/auth/schemas";

export default function RegisterPage() {
  const router = useRouter();
  const { register: createAccount } = useAuth();
  const [error, setError] = useState<unknown>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({ resolver: zodResolver(registerSchema), mode: "onBlur" });

  const onSubmit = handleSubmit(async (values) => {
    setError(null);
    try {
      await createAccount(values);
      router.push("/dashboard");
    } catch (e) {
      setError(e);
    }
  });

  return (
    <AuthFormShell
      title="Create your account"
      subtitle="Frugal explains every recommendation it makes."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="underline underline-offset-4">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <FormError error={error} />

        <Field
          label="Name"
          autoComplete="name"
          placeholder="Priya"
          error={errors.display_name?.message}
          {...register("display_name")}
        />
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
          autoComplete="new-password"
          hint="At least 12 characters, with upper and lower case and a digit."
          error={errors.password?.message}
          {...register("password")}
        />

        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthFormShell>
  );
}
