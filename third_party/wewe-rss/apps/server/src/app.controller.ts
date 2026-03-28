import { Controller, Get, Post, Render, Response } from '@nestjs/common';
import { AppService } from './app.service';
import { ConfigService } from '@nestjs/config';
import { ConfigurationType } from './configuration';
import { Response as Res } from 'express';
import { join } from 'path';
import { TrpcService } from '@server/trpc/trpc.service';

@Controller()
export class AppController {
  constructor(
    private readonly appService: AppService,
    private readonly configService: ConfigService,
    private readonly trpcService: TrpcService,
  ) {}

  @Get()
  getHello(): string {
    return this.appService.getHello();
  }

  @Get('/robots.txt')
  forRobot(): string {
    return 'User-agent:  *\nDisallow:  /';
  }

  @Get('brand/gzhreader-icon.svg')
  getBrandIcon(@Response() res: Res) {
    res.type('image/svg+xml');
    res.sendFile(join(__dirname, '..', 'client', 'gzhreader-icon.svg'));
  }

  @Get('favicon.ico')
  getFavicon(@Response() res: Res) {
    res.type('image/svg+xml');
    res.sendFile(join(__dirname, '..', 'client', 'gzhreader-icon.svg'));
  }

  @Get('/dash*')
  @Render('index.hbs')
  dashRender() {
    const { originUrl: weweRssServerOriginUrl } =
      this.configService.get<ConfigurationType['feed']>('feed')!;
    const { code } = this.configService.get<ConfigurationType['auth']>('auth')!;

    return {
      weweRssServerOriginUrl,
      enabledAuthCode: Boolean(code),
      iconUrl: '/brand/gzhreader-icon.svg',
    };
  }

  @Post('/internal/refresh-all')
  async refreshAll() {
    return this.trpcService.refreshAllMpArticlesAndUpdateFeed();
  }
}
