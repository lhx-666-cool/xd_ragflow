import SvgIcon from '@/components/svg-icon';
import message from '@/components/ui/message';
import { Authorization, Token, UserInfo } from '@/constants/authorization';
import { useAuth } from '@/hooks/auth-hooks';
import {
  useLogin,
  useLoginChannels,
  useLoginWithChannel,
  useRegister,
} from '@/hooks/login-hooks';
import { useSystemConfig } from '@/hooks/system-hooks';
import { rsaPsw } from '@/utils';
import authorizationUtil from '@/utils/authorization-util';
import request from '@/utils/request';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'umi';

import Spotlight from '@/components/spotlight';
import { Button, ButtonLoading } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { zodResolver } from '@hookform/resolvers/zod';
import { Eye, EyeOff } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { BgSvg } from './bg';
import FlipCard3D from './card';
import './index.less';

const DEFAULT_IDS_SERVICE_URL = 'https://xdechat.xidian.edu.cn/';
const DEFAULT_IDS_LOGIN_BASE_URL = 'https://ids.xidian.edu.cn/authserver/login';
const DEFAULT_TICKET_LOGIN_URL = '/v1/user/login';

const ensureTrailingSlash = (value: string) =>
  value.endsWith('/') ? value : `${value}/`;

const getIdsServiceUrl = () =>
  ensureTrailingSlash(
    process.env.UMI_APP_XD_IDS_SERVICE_URL || DEFAULT_IDS_SERVICE_URL,
  );

const getIdsLoginUrl = () =>
  process.env.UMI_APP_XD_IDS_LOGIN_URL ||
  `${DEFAULT_IDS_LOGIN_BASE_URL}?service=${encodeURIComponent(
    getIdsServiceUrl(),
  )}`;

const getTicketLoginUrl = () =>
  process.env.UMI_APP_XD_IDS_TICKET_LOGIN_URL || DEFAULT_TICKET_LOGIN_URL;

const Login = () => {
  const [title, setTitle] = useState('login');
  const navigate = useNavigate();
  const { login, loading: signLoading } = useLogin();
  const { register, loading: registerLoading } = useRegister();
  const { channels, loading: channelsLoading } = useLoginChannels();
  const { login: loginWithChannel, loading: loginWithChannelLoading } =
    useLoginWithChannel();
  const { t } = useTranslation('translation', { keyPrefix: 'login' });
  const [isLoginPage, setIsLoginPage] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [ticketLoginLoading, setTicketLoginLoading] = useState(false);
  const ticketHandledRef = useRef(false);
  const loading =
    signLoading ||
    registerLoading ||
    channelsLoading ||
    loginWithChannelLoading ||
    ticketLoginLoading;
  const { config } = useSystemConfig();
  const registerEnabled = config?.registerEnabled !== 0;

  const { isLogin } = useAuth();
  useEffect(() => {
    if (isLogin) {
      navigate('/');
    }
  }, [isLogin, navigate]);

  useEffect(() => {
    if (isLogin) {
      return;
    }

    const ticket = new URLSearchParams(window.location.search).get('ticket');
    if (!ticket || ticketHandledRef.current) {
      return;
    }
    ticketHandledRef.current = true;

    const loginWithTicket = async () => {
      setTicketLoginLoading(true);
      try {
        const url = new URL(getTicketLoginUrl(), window.location.origin);
        url.searchParams.set('ticket', ticket);
        const { data: res = {}, response } = await request.get(url.toString(), {
          skipToken: true,
        });
        const payload = res?.data ?? res;
        const accessToken = payload?.access_token;
        const uid = payload?.uid;
        const userName = payload?.userName ?? payload?.username;
        const authorizationHeader = response?.headers?.get(Authorization);
        const authorization =
          authorizationHeader || (accessToken ? `Bearer ${accessToken}` : '');
        const resCode = res?.code;
        const success =
          resCode === 0 ||
          resCode === 200 ||
          (resCode === undefined && !!accessToken);

        if (!success) {
          message.error(res?.message || 'Ticket login failed');
          return;
        }

        if (!authorization || !accessToken) {
          message.error('Ticket login response missing token');
          return;
        }

        authorizationUtil.setItems({
          [Authorization]: authorization,
          [Token]: accessToken,
          [UserInfo]: JSON.stringify({
            avatar: payload?.avatar,
            name: userName || uid || '',
            email: uid || '',
          }),
        });

        const currentUrl = new URL(window.location.href);
        currentUrl.searchParams.delete('ticket');
        window.history.replaceState({}, '', currentUrl.toString());
        navigate('/');
      } catch (error) {
        console.error(error);
        message.error('Ticket login failed');
      } finally {
        setTicketLoginLoading(false);
      }
    };

    void loginWithTicket();
  }, [isLogin, navigate]);

  const handleLoginWithChannel = async (channel: string) => {
    await loginWithChannel(channel);
  };

  const handleIdsLogin = () => {
    window.location.href = getIdsLoginUrl();
  };

  const changeTitle = () => {
    setIsLoginPage(title !== 'login');
    if (title === 'login' && !registerEnabled) {
      return;
    }

    setTimeout(() => {
      setTitle(title === 'login' ? 'register' : 'login');
    }, 200);
    // setTitle((title) => (title === 'login' ? 'register' : 'login'));
  };

  const FormSchema = z
    .object({
      nickname: z.string().optional(),
      email: z
        .string()
        .email()
        .min(1, { message: t('emailPlaceholder') }),
      password: z.string().min(1, { message: t('passwordPlaceholder') }),
      remember: z.boolean().optional(),
    })
    .superRefine((data, ctx) => {
      if (title === 'register' && !data.nickname) {
        ctx.addIssue({
          path: ['nickname'],
          message: 'nicknamePlaceholder',
          code: z.ZodIssueCode.custom,
        });
      }
    });
  const form = useForm({
    defaultValues: {
      nickname: '',
      email: '',
      password: '',
      confirmPassword: '',
      remember: false,
    },
    resolver: zodResolver(FormSchema),
  });

  const onCheck = async (params) => {
    console.log('params', params);
    try {
      // const params = await form.validateFields();

      const rsaPassWord = rsaPsw(params.password) as string;

      if (title === 'login') {
        const code = await login({
          email: `${params.email}`.trim(),
          password: rsaPassWord,
        });
        if (code === 0) {
          navigate('/');
        }
      } else {
        const code = await register({
          nickname: params.nickname,
          email: params.email,
          password: rsaPassWord,
        });
        if (code === 0) {
          setTitle('login');
        }
      }
    } catch (errorInfo) {
      console.log('Failed:', errorInfo);
    }
  };

  return (
    <>
      <Spotlight opcity={0.4} coverage={60} color={'rgb(128, 255, 248)'} />
      <Spotlight
        opcity={0.3}
        coverage={12}
        X={'10%'}
        Y={'-10%'}
        color={'rgb(128, 255, 248)'}
      />
      <Spotlight
        opcity={0.3}
        coverage={12}
        X={'90%'}
        Y={'-10%'}
        color={'rgb(128, 255, 248)'}
      />
      <div className=" h-[inherit] relative overflow-auto">
        <BgSvg />

        {/* <SpotlightTopRight opcity={0.7} coverage={10} /> */}
        <div className="absolute top-3 flex flex-col items-center mb-12 w-full text-text-primary">
          <div className="flex items-center mb-4 w-full pl-10 pt-10 ">
            <div className="w-12 h-12 p-2 rounded-lg bg-bg-base border-2 border-border flex items-center justify-center mr-3">
              <img
                src={'/logo.svg'}
                alt="logo"
                className="size-8 mr-[12] cursor-pointer"
              />
            </div>
            <div className="text-xl font-bold self-center">RAGFlow</div>
          </div>
          <h1 className="text-[36px] font-medium  text-center mb-2">
            {t('title')}
          </h1>
          {/* border border-accent-primary rounded-full */}
          {/* <div className="mt-4 px-6 py-1 text-sm font-medium text-cyan-600  hover:bg-cyan-50 transition-colors duration-200 border-glow relative overflow-hidden">
            {t('start')}
          </div> */}
        </div>
        <div className="relative z-10 flex flex-col items-center justify-center min-h-[1050px] px-4 sm:px-6 lg:px-8">
          {/* Logo and Header */}

          {/* Login Form */}
          <FlipCard3D isLoginPage={isLoginPage}>
            <div className="flex flex-col items-center justify-center w-full">
              <div className="text-center mb-8">
                <h2 className="text-xl font-semibold text-text-primary">
                  {title === 'login' ? t('loginTitle') : t('signUpTitle')}
                </h2>
              </div>
              <div className=" w-full max-w-[540px] bg-bg-component backdrop-blur-sm rounded-2xl shadow-xl pt-14 pl-10 pr-10 pb-2 border border-border-button ">
                <Form {...form}>
                  <form
                    className="flex flex-col gap-8 text-text-primary "
                    onSubmit={form.handleSubmit((data) => onCheck(data))}
                  >
                    <FormField
                      control={form.control}
                      name="email"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel required>{t('emailLabel')}</FormLabel>
                          <FormControl>
                            <Input
                              placeholder={t('emailPlaceholder')}
                              autoComplete="email"
                              {...field}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    {title === 'register' && (
                      <FormField
                        control={form.control}
                        name="nickname"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel required>{t('nicknameLabel')}</FormLabel>
                            <FormControl>
                              <Input
                                placeholder={t('nicknamePlaceholder')}
                                autoComplete="username"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    )}

                    <FormField
                      control={form.control}
                      name="password"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel required>{t('passwordLabel')}</FormLabel>
                          <FormControl>
                            <div className="relative">
                              <Input
                                type={showPassword ? 'text' : 'password'}
                                placeholder={t('passwordPlaceholder')}
                                autoComplete={
                                  title === 'login'
                                    ? 'current-password'
                                    : 'new-password'
                                }
                                {...field}
                              />
                              <button
                                type="button"
                                className="absolute inset-y-0 right-0 pr-3 flex items-center"
                                onClick={() => setShowPassword(!showPassword)}
                              >
                                {showPassword ? (
                                  <EyeOff className="h-4 w-4 text-gray-500" />
                                ) : (
                                  <Eye className="h-4 w-4 text-gray-500" />
                                )}
                              </button>
                            </div>
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    {title === 'login' && (
                      <FormField
                        control={form.control}
                        name="remember"
                        render={({ field }) => (
                          <FormItem>
                            <FormControl>
                              <div className="flex gap-2">
                                <Checkbox
                                  checked={field.value}
                                  onCheckedChange={(checked) => {
                                    field.onChange(checked);
                                  }}
                                />
                                <FormLabel
                                  className={cn(' hover:text-text-primary', {
                                    'text-text-disabled': !field.value,
                                    'text-text-primary': field.value,
                                  })}
                                >
                                  {t('rememberMe')}
                                </FormLabel>
                              </div>
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    )}
                    <ButtonLoading
                      type="submit"
                      loading={loading}
                      className="bg-metallic-gradient border-b-[#00BEB4] border-b-2 hover:bg-metallic-gradient hover:border-b-[#02bcdd] w-full my-8"
                    >
                      {title === 'login' ? t('login') : t('continue')}
                    </ButtonLoading>
                    {title === 'login' && (
                      <div className="mt-4">
                        <div className="flex items-center gap-3 text-xs text-text-disabled">
                          <span className="h-px flex-1 bg-border" />
                          <span>Third-party login</span>
                          <span className="h-px flex-1 bg-border" />
                        </div>
                        <Button
                          variant="outline"
                          className="w-full mt-4 h-10"
                          onClick={handleIdsLogin}
                          disabled={ticketLoginLoading}
                        >
                          <img
                            src="https://www.xidian.edu.cn/favicon.ico"
                            alt="Xidian IDS"
                            className="h-4 w-4"
                          />
                          西安电子科技大学统一身份认证
                        </Button>
                      </div>
                    )}
                    {title === 'login' && channels && channels.length > 0 && (
                      <div className="mt-3 border">
                        {channels.map((item) => (
                          <Button
                            variant={'transparent'}
                            key={item.channel}
                            onClick={() => handleLoginWithChannel(item.channel)}
                            style={{ marginTop: 10 }}
                          >
                            <div className="flex items-center">
                              <SvgIcon
                                name={item.icon || 'sso'}
                                width={20}
                                height={20}
                                style={{ marginRight: 5 }}
                              />
                              Sign in with {item.display_name}
                            </div>
                          </Button>
                        ))}
                      </div>
                    )}
                  </form>
                </Form>

                {title === 'login' && registerEnabled && (
                  <div className="mt-10 text-right">
                    <p className="text-text-disabled text-sm">
                      {t('signInTip')}
                      <Button
                        variant={'transparent'}
                        onClick={changeTitle}
                        className="text-accent-primary/90 hover:text-accent-primary hover:bg-transparent font-medium border-none transition-colors duration-200"
                      >
                        {t('signUp')}
                      </Button>
                    </p>
                  </div>
                )}
                {title === 'register' && (
                  <div className="mt-10 text-right">
                    <p className="text-text-disabled text-sm">
                      {t('signUpTip')}
                      <Button
                        variant={'transparent'}
                        onClick={changeTitle}
                        className="text-accent-primary/90 hover:text-accent-primary hover:bg-transparent font-medium border-none transition-colors duration-200"
                      >
                        {t('login')}
                      </Button>
                    </p>
                  </div>
                )}
              </div>
            </div>
          </FlipCard3D>
        </div>
      </div>
    </>
  );
};

export default Login;
