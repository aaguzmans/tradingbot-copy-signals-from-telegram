import MetaTrader5 as mt5
import logging
import re
import json
import time
import threading
import os
from datetime import datetime, timedelta
from telethon import TelegramClient
from typing import Dict, Optional, Tuple, Any, List
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TradeParams:
    """Parámetros de trading extraídos del mensaje"""
    trade_type: str  # 'buy' or 'sell'
    entry_price: Optional[float] = None
    entry_range: Optional[Tuple[float, float]] = None
    stop_loss: float = 0.0
    take_profit: Optional[float] = None
    raw_message: str = ""

    @property
    def is_range_entry(self) -> bool:
        return self.entry_range is not None

    def get_pending_price(self, entry_strategy: str = "auto", central_zone: float = 0.0) -> float:
        """Obtener precio de entrada según estrategia configurada y offset central_zone"""
        if self.is_range_entry:
            min_p, max_p = self.entry_range

            # AUTO: Buy usa min, Sell usa max
            if entry_strategy == "auto":
                price = min_p if self.trade_type == "buy" else max_p
            else:
                base = entry_strategy.strip().lower()
                # Seleccionar precio base según lógica asimétrica
                if base == "min":
                    price = min_p if self.trade_type == "buy" else max_p
                elif base == "max":
                    price = max_p if self.trade_type == "buy" else min_p
                else:
                    # fallback a auto
                    price = min_p if self.trade_type == "buy" else max_p

            # Aplicar central_zone: positivo para BUY, negativo para SELL
            if central_zone != 0:
                if self.trade_type == "buy":
                    price += central_zone
                else:  # sell
                    price -= central_zone

            return price

        # Si no es rango, devolver entry_price
        return self.entry_price


@dataclass
class PendingOrder:
    """Información de orden pendiente"""
    ticket: int
    symbol: str
    trade_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    volume: float
    timestamp: datetime
    is_activated: bool = False


class Logger:
    """Configurador de logging optimizado"""
    
    @staticmethod
    def setup(log_file: str = 'trading_bot_v5_pending.log') -> None:
        """Configurar logging del sistema"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        
        # Silenciar logs innecesarios de Telegram
        for logger_name in ['telethon.network', 'telethon.client', 'telethon']:
            logging.getLogger(logger_name).setLevel(logging.ERROR)


class ConfigManager:
    """Gestor de configuración simplificado"""
    
    def __init__(self, config_file: str = 'config_v2.json'):
        self.config_file = Path(config_file)
        self._config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Cargar y validar configuración"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            required_sections = ['telegram', 'mt5', 'trading']
            for section in required_sections:
                if section not in config:
                    raise ValueError(f"Sección '{section}' faltante en configuración")
            
            logging.info(f"✅ Configuración cargada desde {self.config_file}")
            return config
            
        except Exception as e:
            logging.error(f"❌ Error cargando configuración: {e}")
            raise
    
    @property
    def telegram(self) -> Dict[str, Any]:
        return self._config['telegram']
    
    @property
    def mt5(self) -> Dict[str, Any]:
        return self._config['mt5']
    
    @property
    def trading(self) -> Dict[str, Any]:
        return self._config['trading']


class MessageProcessor:
    """Procesador de mensajes optimizado"""
    
    # Patrones de expresiones regulares compiladas
    TRADE_TYPE_PATTERNS = {
        'buy': re.compile(r'\b(buy|long|bullish|compra|largo)\b', re.IGNORECASE),
        'sell': re.compile(r'\b(sell|short|bearish|venta|corto)\b', re.IGNORECASE)
    }
    
    # Patrones para mensajes de ejecución inmediata (a ignorar)
    IMMEDIATE_PATTERNS = [
        re.compile(r'\b(buy|sell)\s+(gold|xauusd)\s+now\b', re.IGNORECASE),
        re.compile(r'\bgold\s+(buy|sell)\s+now\b', re.IGNORECASE),
        re.compile(r'\bscalping\s+(buy|sell)\b', re.IGNORECASE),
        re.compile(r'\blets?\s+scalping\b', re.IGNORECASE)
    ]
    
    RANGE_PATTERNS = [
        re.compile(r'@\s*([0-9]+\.?[0-9]*)\s*-\s*([0-9]+\.?[0-9]*)', re.IGNORECASE),
        re.compile(r'gold\s*@\s*([0-9]+\.?[0-9]*)\s*-\s*([0-9]+\.?[0-9]*)', re.IGNORECASE)
    ]
    
    ENTRY_PATTERNS = [
        re.compile(r'@\s*([0-9]+\.?[0-9]*)', re.IGNORECASE),
        re.compile(r'(?:entry|enter)\s*:?\s*([0-9]+\.?[0-9]*)', re.IGNORECASE),
        re.compile(r'gold\s*@\s*([0-9]+\.?[0-9]*)', re.IGNORECASE)
    ]
    
    SL_PATTERNS = [
        re.compile(r'(?:sl|stop\s*loss|stop)\s*:?\s*([0-9]+\.?[0-9]*)', re.IGNORECASE),
        re.compile(r'(?:s\.?l\.?)\s*:?\s*([0-9]+\.?[0-9]*)', re.IGNORECASE)
    ]
    
    TP_PATTERNS = [
        re.compile(r'(?:tp|take\s*profit|target)\s*1?\s*:?\s*([0-9]+\.?[0-9]*)', re.IGNORECASE),
        re.compile(r'tp1\s*:?\s*([0-9]+\.?[0-9]*)', re.IGNORECASE)
    ]
    
    # Patrones para actualización de SL
    SL_UPDATE_PATTERNS = [
        re.compile(r'move\s+sl\s+(?:to|at)\s+([0-9]+\.?[0-9]*)', re.IGNORECASE),
        re.compile(r'update\s+sl\s+(?:to|at)\s+([0-9]+\.?[0-9]*)', re.IGNORECASE),
        re.compile(r'sl\s+(?:to|at)\s+([0-9]+\.?[0-9]*)', re.IGNORECASE),
        re.compile(r'new\s+sl\s+(?:to|at|is)\s+([0-9]+\.?[0-9]*)', re.IGNORECASE),
        re.compile(r'change\s+sl\s+(?:to|at)\s+([0-9]+\.?[0-9]*)', re.IGNORECASE)
    ]
    
    def is_immediate_execution_message(self, message: str) -> bool:
        """Verificar si es un mensaje de ejecución inmediata (a ignorar)"""
        for pattern in self.IMMEDIATE_PATTERNS:
            if pattern.search(message):
                return True
        return False
    
    def is_sl_update_message(self, message: str) -> Optional[float]:
        """Verificar si es mensaje de actualización de SL y extraer nuevo valor"""
        for pattern in self.SL_UPDATE_PATTERNS:
            match = pattern.search(message)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None
    
    def extract_parameters(self, message: str) -> Optional[TradeParams]:
        """Extraer parámetros de trading del mensaje"""
        if not message:
            return None
        
        # Ignorar mensajes de ejecución inmediata
        if self.is_immediate_execution_message(message):
            logging.info("⚠️ Mensaje de ejecución inmediata ignorado")
            return None
        
        # Detectar tipo de operación
        trade_type = self._detect_trade_type(message)
        if not trade_type:
            return None
        
        # Extraer parámetros
        entry_data = self._extract_entry_price(message)
        stop_loss = self._extract_stop_loss(message)
        take_profit = self._extract_take_profit(message)
        
        if not entry_data or not stop_loss:
            logging.warning("⚠️ Parámetros insuficientes en el mensaje")
            return None
        
        # Crear objeto TradeParams
        if entry_data['type'] == 'range':
            return TradeParams(
                trade_type=trade_type,
                entry_range=(entry_data['min_price'], entry_data['max_price']),
                stop_loss=stop_loss,
                take_profit=take_profit,
                raw_message=message
            )
        else:
            return TradeParams(
                trade_type=trade_type,
                entry_price=entry_data['price'],
                stop_loss=stop_loss,
                take_profit=take_profit,
                raw_message=message
            )
    
    def _detect_trade_type(self, message: str) -> Optional[str]:
        """Detectar tipo de operación"""
        for trade_type, pattern in self.TRADE_TYPE_PATTERNS.items():
            if pattern.search(message):
                return trade_type
        return None
    
    def _extract_entry_price(self, message: str) -> Optional[Dict[str, Any]]:
        """Extraer precio de entrada"""
        # Verificar rangos primero
        for pattern in self.RANGE_PATTERNS:
            match = pattern.search(message)
            if match:
                try:
                    price1, price2 = float(match.group(1)), float(match.group(2))
                    min_price, max_price = min(price1, price2), max(price1, price2)
                    logging.info(f"📊 Rango detectado: {min_price}-{max_price}")
                    return {'type': 'range', 'min_price': min_price, 'max_price': max_price}
                except ValueError:
                    continue
        
        # Verificar precios únicos
        for pattern in self.ENTRY_PATTERNS:
            match = pattern.search(message)
            if match:
                try:
                    price = float(match.group(1))
                    return {'type': 'single', 'price': price}
                except ValueError:
                    continue
        
        return None
    
    def _extract_stop_loss(self, message: str) -> Optional[float]:
        """Extraer stop loss"""
        for pattern in self.SL_PATTERNS:
            match = pattern.search(message)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None
    
    def _extract_take_profit(self, message: str) -> Optional[float]:
        """Extraer take profit"""
        for pattern in self.TP_PATTERNS:
            match = pattern.search(message)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None


class MT5Manager:
    """Gestor de MetaTrader 5 optimizado para órdenes pendientes"""
    
    def __init__(self, mt5_config: Dict[str, Any], trading_config: Dict[str, Any]):
        self.mt5_config = mt5_config
        self.trading_config = trading_config
        self.symbol = trading_config['symbol']
        self.target_profit = trading_config['target_profit_usd']
        self.connected = False
        self.pending_orders: List[PendingOrder] = []
    
    def connect(self) -> bool:
        """Conectar a MetaTrader 5 en modo silencioso"""
        try:
            # Configurar variables de entorno para reducir interacciones
            os.environ['MT5_NO_GUI'] = '1'
            os.environ['MT5_MINIMIZE'] = '1'
            
            # Inicializar MT5 en modo silencioso con credenciales directas
            if not mt5.initialize(
                login=self.mt5_config['login'],
                password=self.mt5_config['password'],
                server=self.mt5_config['server']
            ):
                error_code, error_desc = mt5.last_error()
                logging.error(f"❌ Error inicializando MT5: {error_desc} ({error_code})")
                return False
            
            logging.info("🔇 MT5 iniciado en modo silencioso con credenciales automáticas")
            
            # Verificar que la conexión fue exitosa
            account_info = mt5.account_info()
            if not account_info:
                error_code, error_desc = mt5.last_error()
                logging.error(f"❌ Error de conexión MT5: {error_desc} (Código: {error_code})")
                return False
            
            # Verificar símbolo
            if not self._setup_symbol():
                return False
            
            logging.info(f"✅ Conectado a MT5 en modo silencioso - Cuenta: {account_info.login} | Servidor: {account_info.server}")
            logging.info(f"💰 Balance: ${account_info.balance:.2f} | Equity: ${account_info.equity:.2f}")
            self.connected = True
            return True
            
        except Exception as e:
            logging.error(f"❌ Error conectando a MT5: {e}")
            return False
    
    def _setup_symbol(self) -> bool:
        """Configurar símbolo de trading con búsqueda avanzada"""
        symbol_info = mt5.symbol_info(self.symbol)
        
        if symbol_info is None:
            # Buscar símbolos alternativos de oro con patrones comunes de brokers
            logging.warning(f"⚠️ Símbolo {self.symbol} no encontrado, buscando alternativas...")
            symbols = mt5.symbols_get()
            
            # Patrones de símbolos de oro más comunes por broker
            gold_patterns = [
                'XAUUSD', 'XAUUSDm', 'XAUUSD.', 'XAUUSD#', 'XAU/USD',
                'GOLD', 'GOLDm', 'GOLD.', 'Au', 'AUU',
            ]
            
            # Buscar por patrones específicos primero
            found_symbol = None
            for pattern in gold_patterns:
                matching_symbols = [s.name for s in symbols if s.name == pattern]
                if matching_symbols:
                    found_symbol = matching_symbols[0]
                    logging.info(f"✅ Encontrado símbolo exacto: {found_symbol}")
                    break
            
            # Si no se encuentra patrón exacto, buscar por contenido
            if not found_symbol:
                gold_symbols = [s.name for s in symbols if 
                              'XAU' in s.name.upper() or 
                              'GOLD' in s.name.upper() or
                              s.name.upper().startswith('AU')]
                
                if gold_symbols:
                    # Priorizar símbolos que contengan USD
                    usd_gold_symbols = [s for s in gold_symbols if 'USD' in s.upper()]
                    found_symbol = usd_gold_symbols[0] if usd_gold_symbols else gold_symbols[0]
                    logging.info(f"✅ Encontrado símbolo por búsqueda: {found_symbol}")
            
            if found_symbol:
                self.symbol = found_symbol
                logging.info(f"🔄 Actualizando configuración a símbolo: {self.symbol}")
                symbol_info = mt5.symbol_info(self.symbol)
            else:
                logging.error(f"❌ No se encontraron símbolos de oro disponibles")
                return False
        
        # Verificar que el símbolo esté visible
        if not symbol_info.visible:
            if not mt5.symbol_select(self.symbol, True):
                logging.error(f"❌ No se pudo seleccionar símbolo {self.symbol}")
                return False
            logging.info(f"✅ Símbolo {self.symbol} seleccionado y visible")
        
        return True
    
    def get_current_price(self) -> Optional[Tuple[float, float]]:
        """Obtener precio actual (bid, ask)"""
        try:
            tick = mt5.symbol_info_tick(self.symbol)
            if tick:
                return tick.bid, tick.ask
            return None
        except Exception as e:
            logging.error(f"❌ Error obteniendo precio: {e}")
            return None
    
    def get_minimum_volume(self) -> float:
        """Obtener volumen mínimo"""
        try:
            symbol_info = mt5.symbol_info(self.symbol)
            return symbol_info.volume_min if symbol_info else 0.01
        except:
            return 0.01
    
    def calculate_tp_for_profit(self, entry_price: float, trade_type: str, volume: float) -> Optional[float]:
        """Calcular TP para ganancia objetivo"""
        try:
            symbol_info = mt5.symbol_info(self.symbol)
            if not symbol_info:
                return None
            
            tick_value = symbol_info.trade_tick_value or 1.0
            tick_size = symbol_info.trade_tick_size or 0.01
            
            points_needed = (self.target_profit * tick_size) / (tick_value * volume)
            
            if trade_type == 'buy':
                tp = entry_price + points_needed
            else:
                tp = entry_price - points_needed
            
            logging.info(f"💰 TP calculado: {tp:.5f} (ganancia: ${self.target_profit})")
            return tp
            
        except Exception as e:
            logging.error(f"❌ Error calculando TP: {e}")
            return None
    
    def place_pending_order(self, trade_params: TradeParams, pending_price: Optional[float] = None) -> bool:
        """Colocar orden pendiente"""
        try:
            prices = self.get_current_price()
            if not prices:
                logging.error("❌ No se pudo obtener precio actual")
                return False
            
            bid, ask = prices
            current_price = ask if trade_params.trade_type == 'buy' else bid
            volume = self.get_minimum_volume()
            
            # Si no se pasó pending_price, calcularlo
            if pending_price is None:
                entry_strategy = self.trading_config.get("entry_strategy", "auto")
                central_zone = self.trading_config.get("central_zone", 0)
                pending_price = trade_params.get_pending_price(entry_strategy, central_zone)
            
            # Calcular TP
            tp = self.calculate_tp_for_profit(pending_price, trade_params.trade_type, volume)
            if not tp:
                logging.error("❌ No se pudo calcular TP")
                return False
            
            # Determinar tipo de orden pendiente
            if trade_params.trade_type == 'buy':
                if pending_price < current_price:
                    order_type = mt5.ORDER_TYPE_BUY_LIMIT
                else:
                    order_type = mt5.ORDER_TYPE_BUY_STOP
            else:  # sell
                if pending_price > current_price:
                    order_type = mt5.ORDER_TYPE_SELL_LIMIT
                else:
                    order_type = mt5.ORDER_TYPE_SELL_STOP
            
            # Preparar orden pendiente
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": self.symbol,
                "volume": volume,
                "type": order_type,
                "price": pending_price,
                "sl": trade_params.stop_loss,
                "tp": tp,
                "deviation": 20,
                "magic": 234007,  # v5.1 pending orders
                "comment": "XAU Bot v5.1 Pending",
                "type_time": mt5.ORDER_TIME_SPECIFIED,
                "expiration": int((datetime.now() + timedelta(hours=4)).timestamp()),
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }
            
            # Ejecutar orden
            result = mt5.order_send(request)
            
            if result is None:
                error_code, error_desc = mt5.last_error()
                logging.error(f"❌ Error ejecutando orden pendiente: {error_desc} ({error_code})")
                return False
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logging.error(f"❌ Error en orden pendiente: {result.comment} ({result.retcode})")
                return False
            
            # Registrar orden pendiente
            pending_order = PendingOrder(
                ticket=result.order,
                symbol=self.symbol,
                trade_type=trade_params.trade_type,
                entry_price=pending_price,
                stop_loss=trade_params.stop_loss,
                take_profit=tp,
                volume=volume,
                timestamp=datetime.now()
            )
            
            self.pending_orders.append(pending_order)
            
            # Log éxito
            logging.info(f"✅ Orden pendiente colocada exitosamente:")
            logging.info(f"   📊 Tipo: {trade_params.trade_type.upper()} {order_type}")
            logging.info(f"   💰 Volumen: {volume}")
            logging.info(f"   🎯 Entrada: {pending_price:.5f}")
            logging.info(f"   🛑 SL: {trade_params.stop_loss:.5f}")
            logging.info(f"   🎯 TP: {tp:.5f}")
            logging.info(f"   🎫 Ticket: {result.order}")
            logging.info(f"   ⏰ Expira en 4 horas")
            
            return True
            
        except Exception as e:
            logging.error(f"❌ Error colocando orden pendiente: {e}")
            return False
    
    def update_pending_order_sl(self, new_sl: float) -> bool:
        """Actualizar SL en órdenes pendientes o posiciones activas"""
        updated_count = 0

        try:
            # 1️⃣ Revisar posiciones activas primero
            positions = mt5.positions_get(symbol=self.symbol)
            if positions:
                for pos in positions:
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "symbol": pos.symbol,
                        "position": pos.ticket,
                        "sl": new_sl,
                        "tp": pos.tp
                    }
                    
                    result = mt5.order_send(request)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        updated_count += 1
                        logging.info(f"✅ SL actualizado en posición activa {pos.ticket} -> {new_sl:.5f}")
                    else:
                        logging.error(f"❌ Error actualizando SL en posición activa {pos.ticket}: {result.comment if result else 'sin respuesta'}")

            # 2️⃣ Revisar órdenes pendientes si no hay posiciones
            pending_orders = mt5.orders_get(symbol=self.symbol)
            if pending_orders:
                for order in pending_orders:
                    request = {
                        "action": mt5.TRADE_ACTION_MODIFY,
                        "order": order.ticket,
                        "symbol": order.symbol,
                        "price": order.price_open,  # obligatorio
                        "sl": new_sl,
                        "tp": order.tp,
                    }

                    result = mt5.order_send(request)
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        updated_count += 1
                        logging.info(f"✅ SL actualizado en orden pendiente {order.ticket} -> {new_sl:.5f}")
                    else:
                        logging.error(f"❌ Error actualizando SL en orden pendiente {order.ticket}: {result.comment if result else 'sin respuesta'}")

            if updated_count > 0:
                logging.info(f"📊 Total de SL actualizados: {updated_count}")
                return True
            else:
                logging.warning("⚠️ No se encontraron posiciones ni órdenes pendientes para actualizar SL")
                return False

        except Exception as e:
            logging.error(f"❌ Error general actualizando SL: {e}")
            return False
    
    def cleanup_expired_orders(self) -> None:
        """Limpiar órdenes expiradas y canceladas"""
        try:
            active_orders = []
            
            for pending_order in self.pending_orders:
                # Verificar si la orden existe
                order_info = mt5.orders_get(ticket=pending_order.ticket)
                position_info = mt5.positions_get(ticket=pending_order.ticket)
                
                if order_info or position_info:
                    # Actualizar estado si es posición
                    if position_info:
                        pending_order.is_activated = True
                    active_orders.append(pending_order)
                else:
                    # Orden cancelada/expirada
                    time_diff = datetime.now() - pending_order.timestamp
                    logging.info(f"🗑️ Orden {pending_order.ticket} removida del seguimiento (duración: {time_diff})")
            
            self.pending_orders = active_orders
            
        except Exception as e:
            logging.error(f"❌ Error limpiando órdenes: {e}")
    
    def get_pending_orders_status(self) -> str:
        """Obtener estado de órdenes pendientes"""
        try:
            if not self.pending_orders:
                return "📊 No hay órdenes pendientes activas"
            
            status = []
            for order in self.pending_orders:
                age = datetime.now() - order.timestamp
                status_text = "Activada" if order.is_activated else "Pendiente"
                status.append(f"🎫 {order.ticket}: {status_text} ({age.total_seconds()//60:.0f}min)")
            
            return "📊 Órdenes activas:\n" + "\n".join(status)
            
        except Exception as e:
            logging.error(f"❌ Error obteniendo estado: {e}")
            return "❌ Error obteniendo estado de órdenes"
    
    def disconnect(self) -> None:
        """Desconectar de MT5"""
        try:
            mt5.shutdown()
            self.connected = False
            logging.info("🔚 MT5 desconectado")
        except:
            pass


class TelegramManager:
    """Gestor de Telegram optimizado"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.client = None
        self.last_message_id = 0
    
    def connect(self) -> bool:
        """Conectar a Telegram"""
        try:
            self.client = TelegramClient(
                'session_v5_pending',
                self.config['api_id'],
                self.config['api_hash']
            )
            
            self.client.start(phone=self.config['phone'])
            
            if self.client.is_connected():
                logging.info(f"✅ Conectado a Telegram - Canal: {self.config['channel_username']}")
                self._initialize_message_id()
                return True
            
            return False
            
        except Exception as e:
            logging.error(f"❌ Error conectando a Telegram: {e}")
            return False
    
    def _initialize_message_id(self) -> None:
        """Inicializar ID del último mensaje"""
        try:
            async def _get_last():
                return await self.client.get_messages(entity=self.config['channel_username'], limit=1)
            
            messages = self.client.loop.run_until_complete(_get_last())
            if messages:
                msg = messages[0]
                self.last_message_id = getattr(msg, 'id', 0)
                logging.info(f"📨 Monitoreando desde mensaje ID: {self.last_message_id}")
            else:
                logging.info("📨 Canal sin mensajes al inicializar, estableciendo last_message_id = 0")
                self.last_message_id = 0

        except Exception as e:
            logging.error(f"⚠️ Error inicializando mensaje ID: {e}")
            import traceback
            logging.error(traceback.format_exc())
            self.last_message_id = 0

    def get_new_messages(self) -> list:
        """Obtener nuevos mensajes"""
        try:
            if not self.client or not self.client.is_connected():
                logging.warning("⚠️ Cliente de Telegram no conectado")
                return []

            async def _fetch():
                return await self.client.get_messages(entity=self.config['channel_username'], limit=5)

            messages = self.client.loop.run_until_complete(_fetch())

            #logging.info(f"📨 Obtenidos {len(messages) if messages else 0} mensajes del canal")
            #logging.info(f"📨 Último mensaje ID procesado: {self.last_message_id}")

            new_messages = []
            # iterar de más viejo a más nuevo
            for msg in reversed(messages or []):
                mid = getattr(msg, 'id', None)
                content = None
                # Telethon puede usar .message, .text o .raw_text según el tipo
                if hasattr(msg, 'message') and msg.message:
                    content = msg.message
                elif hasattr(msg, 'text') and msg.text:
                    content = msg.text
                else:
                    # intentar obtener texto descriptivo
                    content = getattr(msg, 'raw_text', None)

                logging.debug(f"📨 Mensaje ID {mid} contenido detectado: {bool(content)}")

                if mid and mid > self.last_message_id and content:
                    logging.info(f"📨 Nuevo mensaje detectado - ID: {mid}")
                    logging.info(f"📨 Contenido (preview): '{content[:120]}'")
                    new_messages.append(content)
                    self.last_message_id = mid
                elif mid:
                    logging.debug(f"📨 Mensaje ya procesado o vacío - ID: {mid}")

            if not new_messages:
                logging.debug("📨 No hay mensajes nuevos")

            #logging.info(f"📨 Total de mensajes nuevos: {len(new_messages)}")
            return new_messages

        except Exception as e:
            logging.error(f"❌ Error obteniendo mensajes: {e}")
            import traceback
            logging.error(f"Traceback: {traceback.format_exc()}")
            return []
    
    def disconnect(self) -> None:
        """Desconectar de Telegram"""
        try:
            if self.client and self.client.is_connected():
                self.client.disconnect()
            logging.info("🔚 Telegram desconectado")
        except:
            pass


class TradingBot:
    """Bot de trading principal optimizado para órdenes pendientes"""
    
    def __init__(self, config_file: str = 'config_v2.json'):
        self.config_manager = ConfigManager(config_file)
        self.message_processor = MessageProcessor()
        self.mt5_manager = MT5Manager(self.config_manager.mt5, self.config_manager.trading)
        self.telegram_manager = TelegramManager(self.config_manager.telegram)
        self.running = False
        
        # Hilo para limpieza periódica de órdenes
        self.cleanup_thread = None
    
    def start(self) -> None:
        """Iniciar el bot"""
        logging.info("🚀 Iniciando XAU Copy Signal Bot v5.1 - Órdenes Pendientes")
        
        # Conectar servicios
        if not self.mt5_manager.connect():
            logging.error("❌ No se pudo conectar a MT5")
            return
        
        if not self.telegram_manager.connect():
            logging.error("❌ No se pudo conectar a Telegram")
            return
        
        logging.info("✅ Bot iniciado exitosamente")
        logging.info(f"📊 Configuración:")
        logging.info(f"   - Símbolo: {self.mt5_manager.symbol}")
        logging.info(f"   - Ganancia objetivo: ${self.mt5_manager.target_profit}")
        logging.info(f"   - Canal: {self.config_manager.telegram['channel_username']}")
        logging.info("ℹ️ Presiona Ctrl+C para detener")
        
        self.running = True
        
        # Iniciar hilo de limpieza
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
        
        self._main_loop()
    
    def _main_loop(self) -> None:
        """Bucle principal del bot"""
        try:
            while self.running:
                # Obtener nuevos mensajes
                new_messages = self.telegram_manager.get_new_messages()
                
                # Procesar cada mensaje
                for message in new_messages:
                    self._process_message(message)
                
                time.sleep(2)  # Pausa entre verificaciones
                
        except KeyboardInterrupt:
            logging.info("\nℹ️ Bot detenido por el usuario")
        except Exception as e:
            logging.error(f"❌ Error en bucle principal: {e}")
        finally:
            self.stop()
    
    def _cleanup_loop(self) -> None:
        """Bucle de limpieza periódica de órdenes"""
        while self.running:
            try:
                # Limpiar órdenes cada 30 segundos
                time.sleep(30)
                if self.running:
                    self.mt5_manager.cleanup_expired_orders()
                    
                    # Log estado cada 5 minutos
                    if int(time.time()) % 300 == 0:
                        status = self.mt5_manager.get_pending_orders_status()
                        logging.info(status)
                        
            except Exception as e:
                logging.error(f"❌ Error en limpieza: {e}")
                time.sleep(60)  # Esperar más tiempo si hay error
    
    def _process_message(self, message: str) -> None:
        """Procesar mensaje de trading"""
        try:
            logging.info(f"📨 Nuevo mensaje: {message[:100]}...")
            
            # Verificar si es actualización de SL
            new_sl = self.message_processor.is_sl_update_message(message)
            if new_sl:
                logging.info(f"🔄 Mensaje de actualización de SL detectado: {new_sl}")
                success = self.mt5_manager.update_pending_order_sl(new_sl)
                if success:
                    logging.info("✅ SL actualizado exitosamente")
                else:
                    logging.error("❌ Error actualizando SL")
                return
            
            # Extraer parámetros de trading
            trade_params = self.message_processor.extract_parameters(message)
            if not trade_params:
                logging.info("ℹ️ Mensaje no contiene parámetros de trading válidos o es de ejecución inmediata")
                return
            
            logging.info(f"✅ Parámetros extraídos:")
            logging.info(f"   📊 Tipo: {trade_params.trade_type.upper()}")
            
            # 👇 Aquí cargamos la estrategia desde config
            entry_strategy = self.config_manager.trading.get("entry_strategy", "auto")
            central_zone = self.config_manager.trading.get("central_zone", 0)

            if trade_params.is_range_entry:
                min_p, max_p = trade_params.entry_range
                pending_price = trade_params.get_pending_price(entry_strategy, central_zone)
                logging.info(f"   🎯 Rango: {min_p:.1f} - {max_p:.1f}")
                logging.info(f"   📍 Precio pendiente (mínimo): {pending_price:.1f}")
            else:
                logging.info(f"   📈 Precio: {trade_params.entry_price:.1f}")
            
            logging.info(f"   🛑 SL: {trade_params.stop_loss:.1f}")
            if trade_params.take_profit:
                logging.info(f"   🎯 TP: {trade_params.take_profit:.1f}")
            
            # Colocar orden pendiente
            success = self.mt5_manager.place_pending_order(trade_params, pending_price)
            
            if success:
                logging.info("🎉 Orden pendiente colocada exitosamente")
                status = self.mt5_manager.get_pending_orders_status()
                logging.info(status)
            else:
                logging.error("❌ Error colocando orden pendiente")
                
        except Exception as e:
            logging.error(f"❌ Error procesando mensaje: {e}")
    
    def stop(self) -> None:
        """Detener el bot"""
        self.running = False
        
        # Esperar a que termine el hilo de limpieza
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            self.cleanup_thread.join(timeout=5)
        
        self.mt5_manager.disconnect()
        self.telegram_manager.disconnect()
        logging.info("🔚 Bot detenido")


def main():
    """Función principal"""
    # Configurar logging
    Logger.setup()
    
    try:
        # Crear y ejecutar bot
        bot = TradingBot()
        bot.start()
        
    except Exception as e:
        logging.error(f"❌ Error fatal: {e}")


if __name__ == "__main__":
    main()